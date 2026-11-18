#!/bin/zsh
# One-off jobs that run once on or after a due time, then delete themselves.
# Each job is a private directory under $root/jobs holding an executable `run`,
# a `job` record (see job_keys) and any material the job needs; a successful
# run deletes the whole directory, and a failure keeps it and sends an ntfy
# warning. A run exiting 75 (EX_TEMPFAIL) means "condition not met yet": it
# spends no attempt and sends no alert, and the next check retries, until the
# job's give-up time, after which 75 is an ordinary failure. The LaunchAgent com.jacobr.deferred-jobs calls `run-due` hourly and
# at login; the user crontab calls `watchdog` daily, so a checker that stopped
# running still produces a warning. Job output never goes into a warning.
set -eu -o pipefail
setopt extended_glob
zmodload zsh/system zsh/datetime
zmodload -F zsh/stat b:zstat
umask 077

script=${0:A}
repo=${script:h:h:h}
root=${DEFERRED_ROOT:-$HOME/.agent/deferred}
jobs_dir=$root/jobs
flagged_dir=$root/flagged   # one marker per invalid job already warned about
log=$root/deferred.log
heartbeat=$root/last-check
label=${DEFERRED_LABEL:-com.jacobr.deferred-jobs}
plist_source=$repo/macbook/launchagents/com.jacobr.deferred-jobs.plist
plist_target=$HOME/Library/LaunchAgents/$label.plist
notifier=$repo/macbook/scripts/ntfy_warning.py
secrets=${DEFERRED_SECRETS:-$repo/pi/secrets/.bash_variables}
job_path=/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin
stale_after=10800       # watchdog: checker silent this long (s) means it stopped
wake_grace=${DEFERRED_WAKE_GRACE:-1800}   # launchd catches up shortly after wake
stamp_format=%Y-%m-%dT%H:%M
default_attempts=3
default_timeout=900
lock_wait=${DEFERRED_LOCK_WAIT:-10}
# Tests only: a file holding seconds added to the clock the give-up time is judged by, here
# and (through the pass environment) by a vanpi run at its launch. Unset, the clock is real.
clock_shift_file=${DEFERRED_CLOCK_SHIFT_FILE-}
# Every job record defines these keys; `state` is pending or failed. `give_up`
# is optional (records written before it existed lack it): empty means due + 7d.
# `last_not_ready` (stamp of the latest check that exited not-ready) and
# `not_ready_checks` (how many such checks) are optional too, empty when none.
# `kind` (script or agent) and, for agent jobs, `host` (mac or vanpi) say what
# the job is; records written before they existed lack them and are script
# jobs, or Mac agent jobs when they hold an agent.env, exactly as before.
job_keys=(name due description created origin attempts max_attempts timeout state)
optional_job_keys=(give_up last_not_ready not_ready_checks kind host)
not_ready_status=75     # a job's own "condition not met yet" exit status
run_now_not_ready=4     # run-now's exit status for it (75 there means the lock is busy)
default_give_up_seconds=$(( 7 * 86400 ))
agent_template=$repo/macbook/scripts/deferred_agent_run.zsh
agent_notify=$repo/macbook/scripts/deferred_agent_notify.zsh
pi_template=$repo/macbook/scripts/deferred_pi_run.py
pi_modules=($repo/macbook/scripts/deferred_pi_state.py $repo/macbook/scripts/deferred_pi_remote.py)  # copied beside it
pi_control=$repo/macbook/scripts/deferred_pi_control.py   # reads and acts on a vanpi job's state
list_json=$repo/macbook/scripts/deferred_list_json.py   # turns the list stream into JSON
agent_hosts=(mac vanpi)   # where an add-agent job runs its prompt
pi_tiers=(astra sol luna) # pi_agent.py --agent values; a vanpi job's --model
refused_status=6          # cancel: a vanpi run may still be active (--abandon overrides)
relaunch_refused_status=7 # retry/run-now: a vanpi attempt is unresolved and --relaunch did not name it
reschedule_refused_status=8 # reschedule: a vanpi job that has launched moves on only by retry or run-now
# deferred_pi_run.py's MIN_PASS_TIMEOUT: a vanpi pass needs this long to launch and read the run once,
# and READY_ALLOWANCE more when it first runs a --ready command.
pi_min_timeout=390 pi_ready_allowance=30
typeset -A job          # the record load_job last read
typeset run_job_status=0 run_job_not_ready=0 run_job_collecting=0 run_job_done=0  # what the last pass left
typeset add_kind=script add_host=   # what cmd_add records; stage_agent_job sets them for agent jobs
typeset -a pass_env=()  # extra NAME=VALUE settings for a pass; a caller sets them as a local
source $repo/macbook/scripts/deferred_pi_schedule.zsh

usage() {
  print -u2 -- "usage: ${script:t} add [--attempts N] [--timeout SECONDS] [--give-up DUE] [--file PATH]... DUE NAME SCRIPT [DESCRIPTION]
       ${script:t} add-agent [--host mac|vanpi] [--ready CMD] [--model M] [--effort E] [--topic-var VAR]
                  [--cwd DIR] [--run-timeout MINUTES] [--attempts N] [--timeout SECONDS] [--give-up DUE]
                  DUE NAME PROMPT_FILE|vanpi:/PATH [DESCRIPTION]
       ${script:t} list [--json]
       ${script:t} cancel [--abandon] JOB | retry JOB [--relaunch ID] | run-now JOB [--relaunch ID]
       ${script:t} reschedule JOB DUE
       ${script:t} status | run-due | watchdog | install | uninstall
DUE is YYYY-MM-DD, 'YYYY-MM-DD HH:MM' (local time), +Nd or +Nh.
A job exiting $not_ready_status means not ready yet (no attempt spent, no alert) until --give-up (default DUE+7d)."
  exit 2
}

die() { print -u2 -- "${script:t}: $*"; exit 1 }
log_line() { print -r -- "$(strftime '%F %T' $EPOCHSECONDS) $*" >> $log }

load_job() {  # DIR: fills $job from DIR/job; fails unless every job_keys entry is present
  local line key
  job=()
  { while IFS= read -r line; do
      [[ $line == ?*=* ]] && job[${line%%=*}]=${line#*=}
    done } 2>/dev/null < $1/job || return 1
  for key in $job_keys; do (( ${+job[$key]} )) || return 1; done
  for key in $optional_job_keys; do (( ${+job[$key]} )) || job[$key]=; done
}

give_up_stamp() {  # prints the time after which a not-ready exit counts as a failure
  [[ -z $job[give_up] ]] || { print -r -- $job[give_up]; return }
  local epoch out
  strftime -r -s epoch $stamp_format $job[due]
  strftime -s out $stamp_format $(( epoch + default_give_up_seconds ))
  print -r -- $out
}

clear_not_ready() { job[last_not_ready]=; job[not_ready_checks]= }

window_now() {  # prints the time, as a stamp, that the give-up time is compared with
  local shift=0
  [[ -z $clock_shift_file || ! -f $clock_shift_file ]] || shift=$(<$clock_shift_file)
  [[ $shift == <-> ]] || shift=0
  strftime $stamp_format $(( EPOCHSECONDS + shift ))
}

record_not_ready() {  # the check just made exited not-ready: remember when and how often
  job[last_not_ready]=$(strftime $stamp_format $EPOCHSECONDS)
  job[not_ready_checks]=$(( ${job[not_ready_checks]:-0} + 1 ))
}

agent_setting() {  # DIR KEY: sets REPLY to an add-agent setting as typed; empty when none
  # Read line by line, never sourced: listing a job must not run anything from it.
  local line
  REPLY=
  [[ -f $1/agent.env ]] || return 0
  while IFS= read -r line; do
    [[ $line == $2=* ]] && { REPLY=${(Q)${line#$2=}}; return 0 }
  done < $1/agent.env
}

save_job() {  # DIR: rewrites DIR/job from $job atomically
  local key
  local -a lines
  for key in $job_keys; do lines+=("$key=$job[$key]"); done
  for key in $optional_job_keys; do [[ -z ${job[$key]-} ]] || lines+=("$key=$job[$key]"); done
  print -rl -- $lines > $1/job.tmp
  /bin/mv -f -- $1/job.tmp $1/job
}

normalize_due() {  # prints YYYY-MM-DDTHH:MM, or fails for a date that does not exist
  local due=$1 epoch back
  case $due in
    +<1->d) strftime -s due $stamp_format $(( EPOCHSECONDS + ${${due#+}%d} * 86400 )) ;;
    +<1->h) strftime -s due $stamp_format $(( EPOCHSECONDS + ${${due#+}%h} * 3600 )) ;;
    <->-<->-<->) due+=T00:00 ;;
    <->-<->-<->' '<->:<->) due=${due/ /T} ;;
  esac
  strftime -r -s epoch $stamp_format $due 2>/dev/null || return 1
  strftime -s back $stamp_format $epoch
  [[ $back == $due ]] || return 1  # strptime quietly rolls 02-30 into March
  print -r -- $due
}

private_path() {  # ours, not a symlink, not writable by group or others
  [[ -e $1 && ! -L $1 && -O $1 ]] || return 1
  local mode
  mode=$(zstat -L +mode -- $1) || return 1
  (( (mode & 18) == 0 ))  # 18 = octal 022
}

ensure_root() {
  /bin/mkdir -p -m 700 -- $root $jobs_dir $flagged_dir
  private_path $root && private_path $jobs_dir || die "$root must be private to $USERNAME"
}

acquire_job_lock() {  # WAIT: hold the checker lock until this process exits
  local wait=$1
  ensure_root
  : >> $root/lock
  zsystem flock -t $wait $root/lock 2>/dev/null
}

acquire_mutation_lock() {
  acquire_job_lock $lock_wait || {
    print -u2 -- "${script:t}: the checker is running jobs; try again shortly"
    exit 75
  }
}

send_alert() {  # TITLE MESSAGE: high priority; fails when it was not sent. The sender's reason, never the URL, goes to the log
  print -r -- $2 | /usr/bin/python3 -I -B $notifier --secrets $secrets --title $1 2>>$log && return 0
  log_line "warning undelivered: $1"
  return 1
}

alert() { send_alert "$@" || true }  # TITLE MESSAGE: best effort

agent_loaded() { /bin/launchctl print gui/$UID/$label >/dev/null 2>&1 }
valid_name() { [[ $1 == [a-z0-9][a-z0-9-]# && ${#1} -le 60 ]] }
# A dated id (YYYY-MM-DD-NAME): longer than a name may be, so checked on its own.
valid_dated_id() { [[ $1 == [0-9](#c4)-[0-9](#c2)-[0-9](#c2)-* ]] && valid_name ${1#*-*-*-} }

checker_summary() {
  if [[ -f $heartbeat ]]; then print -r -- "checker last ran $(<$heartbeat)"; else print -r -- "checker has never run"; fi
}

checker_stale() {  # true when the checker has not run within stale_after
  local last=0
  [[ -f $heartbeat ]] && last=$(zstat -L +mtime -- $heartbeat)
  (( EPOCHSECONDS - last > stale_after ))
}

cmd_add() {
  local attempts=$default_attempts timeout=$default_timeout file give_up=
  local -a files
  while [[ ${1-} == --* ]]; do
    case $1 in
      --attempts) [[ ${2-} == <1-> ]] || die "--attempts needs a positive number"; attempts=$2; shift 2 ;;
      --timeout) [[ ${2-} == <1-> ]] || die "--timeout needs seconds"; timeout=$2; shift 2 ;;
      --give-up) give_up=$(normalize_due ${2-}) || die "invalid --give-up time: ${2-}"; shift 2 ;;
      --file) [[ -f ${2-} ]] || die "--file needs an existing file"; files+=(${2:A}); shift 2 ;;
      *) usage ;;
    esac
  done
  (( $# >= 3 )) || usage
  local due name=$2 src=$3 description=${${4-}//$'\n'/ }
  due=$(normalize_due $1) || die "invalid due time: $1"
  valid_name $name || die "name must be lowercase letters, digits and dashes"
  [[ -z $give_up || ! $give_up < $due ]] || die "--give-up must not be before the due time"
  [[ -f $src && $(/usr/bin/head -c 2 $src) == '#!' ]] || die "$src must be a script starting with #!"
  for file in $files; do
    [[ ${file:t} != (run|job|last.log) ]] || die "--file $file collides with a job file name"
  done
  ensure_root
  local dir=$jobs_dir/${due%%T*}-$name
  [[ ! -e $dir ]] || die "job ${dir:t} already exists"
  /bin/mkdir -m 700 -- $dir
  /usr/bin/install -m 700 -- $src $dir/run
  for file in $files; do /usr/bin/install -m 600 -- $file $dir/${file:t}; done
  job=(name "$name" due "$due" description "$description" created "$(strftime $stamp_format $EPOCHSECONDS)"
    origin "$PWD" attempts 0 max_attempts "$attempts" timeout "$timeout" state pending give_up "$give_up"
    kind "$add_kind" host "$add_host")
  save_job $dir
  log_line "added ${dir:t} due $due"
  print -r -- $dir
  agent_loaded || print -u2 -- "warning: $label is not loaded, so nothing will run it; run: ${script:t} install"
}

one_line_path() {  # VALUE: non-empty, one line, at most 1024 characters
  [[ -n $1 && $1 != *$'\n'* && ${#1} -le 1024 ]]
}

cmd_add_agent() {
  local host= model= effort=medium topic_var=NTFY_MACBOOK_URL ready= cwd= run_timeout= prompt pi_prompt= timeout= attempts=
  local -a add_opts
  while [[ ${1-} == --* ]]; do
    case $1 in
      --host) (( ${agent_hosts[(Ie)${2-}]} )) || die "--host needs ${(j: or :)agent_hosts}"; host=$2; shift 2 ;;
      --ready) [[ -n ${2-} ]] || die "--ready needs a command"; ready=$2; shift 2 ;;
      --model) [[ ${2-} == [A-Za-z0-9._\[\]-]## ]] || die "--model needs a model name"; model=$2; shift 2 ;;
      --effort) [[ ${2-} == (low|medium|high|xhigh|max) ]] || die "--effort needs low, medium, high, xhigh or max"; effort=$2; shift 2 ;;
      --topic-var) [[ ${2-} == [A-Z_][A-Z0-9_]# ]] || die "--topic-var needs a variable name"; topic_var=$2; shift 2 ;;
      --cwd) [[ -n ${2-} ]] || die "--cwd needs a directory"; cwd=$2; shift 2 ;;
      --run-timeout) [[ ${2-} == <1-> ]] || die "--run-timeout needs minutes"; run_timeout=$2; shift 2 ;;
      --attempts) add_opts+=($1 ${2-}); attempts=${2-}; shift 2 ;;
      --give-up) add_opts+=($1 ${2-}); shift 2 ;;
      --timeout) add_opts+=($1 ${2-}); timeout=${2-}; shift 2 ;;
      *) usage ;;
    esac
  done
  (( $# >= 3 )) || usage
  prompt=$3
  if [[ $prompt == vanpi:* ]]; then
    [[ $host != mac ]] || die "a vanpi: prompt file runs on vanpi; it cannot be used with --host mac"
    host=vanpi pi_prompt=${prompt#vanpi:}
    [[ $pi_prompt == /* ]] && one_line_path $pi_prompt ||
      die "a vanpi: prompt must be an absolute path on one line, at most 1024 characters"
  fi
  valid_name $2 || die "name must be lowercase letters, digits and dashes"
  host=${host:-mac}
  if [[ $host == vanpi ]]; then
    model=${model:-sol} cwd=${cwd:-'~'}   # the Pi's home; stored as typed, never resolved here
    (( ${pi_tiers[(Ie)$model]} )) || die "--model for --host vanpi is the pi-agent tier: ${(j:, :)pi_tiers}"
    one_line_path $cwd || die "--cwd for --host vanpi must be one line, at most 1024 characters"
    # pi_agent.py rewrites a path under this Mac's home to the Pi's home, so such a path is ambiguous.
    [[ $cwd != $HOME && $cwd != $HOME/* ]] ||
      die "--cwd $cwd is under this Mac's home folder; for --host vanpi write ~/... or a real path on the Pi"
    [[ -z $attempts || $attempts == 1 ]] ||
      die "--attempts does not apply to --host vanpi: such a job starts at most one Pi run by itself; retry or run-now starts another"
    pi_check_timeout "${timeout:-$default_timeout}" "$ready"
    add_opts=(--attempts 1 $add_opts)
    run_timeout=${run_timeout:-120}
  else
    [[ -z $run_timeout ]] || die "--run-timeout applies only to --host vanpi"
    model=${model:-haiku} cwd=${cwd:-$PWD}
    [[ -d $cwd ]] || die "working directory $cwd does not exist"
    cwd=${cwd:A}
  fi
  [[ -n $pi_prompt || -f $prompt ]] || die "prompt file $prompt does not exist"
  stage_agent_job "$@"
}

pi_check_timeout() {  # PASS_TIMEOUT READY: a vanpi --timeout must leave room to launch (and run --ready)
  [[ $1 == <-> ]] || return 0   # cmd_add rejects it
  local floor=$pi_min_timeout
  [[ -z ${2-} ]] || floor=$(( floor + pi_ready_allowance ))
  (( $1 >= floor )) && return 0
  [[ -n ${2-} ]] && die "--timeout for --host vanpi with --ready must be at least $floor seconds (to run --ready, launch and read the run once)"
  die "--timeout for --host vanpi must be at least $floor seconds (to launch and read the run once)"
}

stage_agent_job() {  # DUE NAME PROMPT [DESCRIPTION], with cmd_add_agent's settings in scope
  local stage rc=0 template=$agent_template module add_kind=agent add_host=$host
  local -a files
  if [[ $host == vanpi ]]; then
    template=$pi_template
    for module in $pi_modules; do files+=(--file $module); done
  fi
  stage=$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/deferred-agent.XXXXXX")
  /usr/bin/install -m 700 -- $template $stage/run
  if [[ -z $pi_prompt ]]; then
    /usr/bin/install -m 600 -- $3 $stage/prompt.txt
    files+=(--file $stage/prompt.txt)
  fi
  print -rl -- "agent_name=${(q)2}" "agent_host=${(q)host}" "agent_cwd=${(q)cwd}" "agent_model=${(q)model}" \
    "agent_effort=${(q)effort}" "agent_ready=${(q)ready}" "agent_topic_var=${(q)topic_var}" \
    "agent_notifier=${(q)notifier}" "agent_secrets=${(q)secrets}" "agent_notify=${(q)agent_notify}" \
    "agent_pi_prompt=${(q)pi_prompt}" "agent_run_timeout=${(q)run_timeout}" > $stage/agent.env
  (cmd_add $add_opts $files --file $stage/agent.env $1 $2 $stage/run \
    "${4:-agent follow-up: $2}") || rc=$?
  [[ -n $stage && $stage == */deferred-agent.* ]] && /bin/rm -rf -- $stage
  return $rc
}

emit_json_field() { print -rn -- "$1"$'\0'; }

emit_list_json_records() {
  local loaded=0 stale=0 has_last_run=0 last_run= dir
  agent_loaded && loaded=1
  checker_stale && stale=1
  if [[ -f $heartbeat ]]; then has_last_run=1; last_run=$(<$heartbeat); fi
  emit_json_field H
  emit_json_field $loaded
  emit_json_field $has_last_run
  emit_json_field "$last_run"
  emit_json_field $stale
  emit_json_field "$jobs_dir"
  for dir in $jobs_dir/*(N/); do
    if ! job_runnable $dir; then
      emit_json_field I
      emit_json_field "${dir:t}"
      continue
    fi
    emit_json_field V
    emit_json_field "${dir:t}"
    emit_json_field "$job[name]"
    emit_json_field "$job[due]"
    emit_json_field "$job[state]"
    emit_json_field "$job[attempts]"
    emit_json_field "$job[max_attempts]"
    emit_json_field "$job[timeout]"
    emit_json_field "$job[description]"
    emit_json_field "$job[created]"
    emit_json_field "$job[origin]"
    emit_json_field "$dir/last.log"
    emit_json_field "$(give_up_stamp)"
    job_host $dir
    emit_json_field "$([[ -n $REPLY ]] && print 1 || print 0)"
    [[ -z $REPLY ]] || agent_setting $dir agent_ready   # not in $(...), which would strip trailing newlines
    emit_json_field "$REPLY"
    emit_json_field "$job[last_not_ready]"
    emit_json_field "${job[not_ready_checks]:-0}"
    emit_agent_fields $dir
  done
}

job_host() {  # DIR: REPLY is the agent job's host (mac or vanpi), empty for a script job
  if [[ -n $job[kind] ]]; then
    REPLY=${${job[kind]:#script}:+$job[host]}
  else
    REPLY=   # old records: an agent.env made a Mac agent job
    [[ ! -f $1/agent.env ]] || REPLY=mac
  fi
  return 0
}

emit_agent_fields() {  # DIR: host, model, effort, and the folder whose Pi state the assembler reads
  local key host
  job_host $1; host=$REPLY
  emit_json_field "$host"
  for key in agent_model agent_effort; do
    REPLY=
    [[ -z $host ]] || agent_setting $1 $key
    emit_json_field "$REPLY"
  done
  emit_json_field "${${host:#mac}:+$1}"
}

print_list_json() {
  emit_list_json_records | /usr/bin/python3 -I -B $list_json
}

cmd_list() {
  (( $# <= 1 )) || usage
  if (( $# == 1 )); then
    [[ $1 == --json ]] || usage
    print_list_json
    return
  fi
  local dir
  checker_summary
  for dir in $jobs_dir/*(N/); do
    if ! load_job $dir; then print -r -- "?  ${dir:t}  (incomplete job record)"; continue; fi
    printf '%s  %-7s  %s/%s  %s  %s\n' "${job[due]/T/ }" "$job[state]" \
      "$job[attempts]" "$job[max_attempts]" "${dir:t}" "$job[description]"
  done
}

resolve_job() {  # JOB: set REPLY to the one exact id or exact name match
  valid_name $1 || valid_dated_id $1 || die "no job named $1"
  local -a matches=($jobs_dir/$1(N/) $jobs_dir/<->-<->-<->-$1(N/))
  (( ${#matches} == 1 )) || die "no single job matches $1"
  REPLY=$matches[1]
}

cmd_cancel() {
  local abandon=
  [[ ${1-} == --abandon ]] && { abandon=--abandon; shift }
  (( $# == 1 )) || usage
  acquire_mutation_lock
  resolve_job $1
  local dir=$REPLY
  # A Pi run outlives the job directory, so it is stopped (or found finished) first.
  # A record that cannot be trusted beside signs of a vanpi job is kept unless --abandon.
  if load_job $dir && job_record_valid $dir; then
    ! pi_job || pi_cancel $dir "$abandon"
  elif pi_evidence $dir; then
    pi_cancel $dir "$abandon" untrusted
  fi
  /bin/rm -rf -- $dir
  log_line "cancelled ${dir:t}"
  print -r -- "cancelled ${dir:t}"
}

relaunch_args() {  # ARGS: sets relaunch (a vanpi attempt's run id or token, or empty) and REPLY (the one JOB)
  relaunch= REPLY=
  local -a rest
  while (( $# )); do
    case $1 in
      --relaunch) [[ ${2-} == [a-z0-9][a-z0-9-]# && ${#2} -le 80 ]] ||
          die "--relaunch needs the run id or launch token that ${script:t} list --json shows as relaunch_id"
        relaunch=$2; shift 2 ;;
      *) rest+=($1); shift ;;
    esac
  done
  (( ${#rest} == 1 )) || usage
  REPLY=$rest[1]
}

require_runnable_job() {  # DIR: load it or fail with a user-facing error
  job_runnable $1 || die "job ${1:t} is not runnable (invalid record or permissions)"
}

cmd_retry() {
  local relaunch
  relaunch_args "$@"
  acquire_mutation_lock
  resolve_job $REPLY
  local dir=$REPLY
  require_runnable_job $dir
  [[ $job[state] != pending ]] || die "job ${dir:t} is already pending"
  [[ $job[state] == failed ]] || die "job ${dir:t} has invalid state: $job[state]"
  if pi_job; then pi_rearm $dir retry "$relaunch"
  elif [[ -n $relaunch ]]; then die "--relaunch applies only to vanpi jobs"; fi
  job[state]=pending
  job[attempts]=0
  clear_not_ready
  save_job $dir
  log_line "retrying ${dir:t} at the next check"
  print -r -- "retrying ${dir:t} at the next check"
}

cmd_reschedule() {
  (( $# == 2 )) || usage
  local due
  due=$(normalize_due $2) || die "invalid due time: $2"
  acquire_mutation_lock
  resolve_job $1
  local dir=$REPLY new_dir
  require_runnable_job $dir
  if pi_job; then
    pi_snapshot $dir || die "cannot read the Pi run state of ${dir:t}"
    if [[ $pi[state] != unlaunched ]]; then
      print -u2 -- "${script:t}: job ${dir:t} has launched on vanpi (Pi run: $pi[state]); reschedule never starts another run: use retry ${dir:t} or run-now (${script:t} list --json shows whether --relaunch is needed: relaunch_id)"
      exit $reschedule_refused_status
    fi
  fi
  new_dir=$jobs_dir/${due%%T*}-$job[name]
  [[ $new_dir == $dir || ! -e $new_dir ]] || die "job ${new_dir:t} already exists"
  job[due]=$due
  job[give_up]=   # a new due time gets the default window again
  clear_not_ready
  if [[ $job[state] == failed ]]; then
    job[state]=pending
    job[attempts]=0
  fi
  save_job $dir
  if [[ $new_dir != $dir ]]; then /bin/mv -- $dir $new_dir; fi
  log_line "rescheduled ${dir:t} as ${new_dir:t} due $due"
  print -r -- "${new_dir:t}"
}

execute_job() {  # DIR TIMEOUT [ARG]: one pass of the job's run (ARG: collect); returns its exit status
  local dir=$1 timeout=$2 pid watcher rc=0 flag
  local -a run_args=(${3-})
  flag=$(/usr/bin/mktemp "${TMPDIR:-/tmp}/deferred-timeout.XXXXXX")
  /bin/rm -f -- $flag
  # The job gets its own session so a timeout can stop everything it started
  # (an agent job's claude child included), not just its run script.
  (
    cd -- $dir
    exec /usr/bin/perl -e 'use POSIX (); POSIX::setsid(); exec @ARGV or exit 127' -- \
      /usr/bin/env -i HOME=$HOME USER=$USERNAME LOGNAME=$USERNAME LANG=en_US.UTF-8 \
      PATH=$job_path TMPDIR=${TMPDIR:-/tmp} DEFERRED_JOB_DIR=$dir DEFERRED_LOG=$log $pass_env ./run $run_args
  ) > $dir/last.log 2>&1 &
  pid=$!
  { sleep $timeout; : > $flag; kill -TERM -- -$pid; sleep 2; kill -KILL -- -$pid } >/dev/null 2>&1 &
  watcher=$!
  wait $pid || rc=$?
  if [[ -e $flag ]]; then
    wait $watcher >/dev/null 2>&1 || true   # let the grace period and KILL finish
  else
    /usr/bin/pkill -P $watcher >/dev/null 2>&1 || true
    kill $watcher >/dev/null 2>&1 || true
  fi
  [[ $flag == */deferred-timeout.* ]] && /bin/rm -f -- $flag
  return $rc
}

run_job() {  # DIR MODE: one scheduled/manual attempt; purge success, warn only scheduled
  local dir=$1 mode=${2:-scheduled} rc=0
  run_job_status=0 run_job_not_ready=0 run_job_collecting=0 run_job_done=0
  job[attempts]=$(( job[attempts] + 1 ))
  save_job $dir  # recorded first, so a crash mid-run still counts
  execute_job $dir $job[timeout] || rc=$?
  run_job_status=$rc
  if pi_job; then pi_settle $dir $mode $rc; else account_pass $dir $mode $rc; fi
}

account_pass() {  # DIR MODE RC: not ready (until give-up), done, or a failed attempt with its alert
  local dir=$1 mode=$2 rc=$3
  if (( rc == not_ready_status )) && [[ $(window_now) < $(give_up_stamp) ]]; then
    run_job_not_ready=1
    job[attempts]=$(( job[attempts] - 1 ))
    record_not_ready
    save_job $dir
    log_line "${${mode:#scheduled}:+manual }not ready ${dir:t}; the next check retries"
    return 0
  fi
  clear_not_ready   # the condition was met (or the window closed), so the last check is no longer "not ready"
  (( rc == 0 )) || save_job $dir
  local tries="attempt $job[attempts]/$job[max_attempts]" prefix=
  [[ $mode == manual ]] && prefix="manual "
  if (( rc == 0 )); then
    [[ ${dir:h} == $jobs_dir && -n ${dir:t} ]] || die "refusing to delete $dir"
    /bin/rm -rf -- $dir
    run_job_done=1
    log_line "${prefix}done ${dir:t} ($tries)"
    return 0
  fi
  log_line "${prefix}failed ${dir:t} exit $rc ($tries); output in $dir/last.log"
  if (( job[attempts] >= job[max_attempts] )); then
    job[state]=failed
    save_job $dir
    if [[ $mode == scheduled ]]; then
      alert "Deferred job gave up: ${dir:t}" \
        "Exit $rc on $tries; it will not retry. Job kept at $dir (see last.log); review it in Mac Panel (macpanel)."
    fi
  elif [[ $mode == scheduled ]]; then
    alert "Deferred job failed: ${dir:t}" \
      "Exit $rc on $tries; retrying at the next hourly check. Review $dir/last.log in Mac Panel (macpanel)."
  fi
}

job_record_valid() {  # DIR: validate fields that later become paths, numbers or states
  local dir=$1
  valid_name "$job[name]" || return 1
  [[ $job[state] == (pending|failed) ]] || return 1
  [[ $job[attempts] == <-> && $job[max_attempts] == <1-> && $job[timeout] == <1-> ]] || return 1
  [[ $job[due] == <->-<->-<->T<->:<-> ]] || return 1
  [[ -z $job[give_up] ]] || normalize_due "$job[give_up]" >/dev/null || return 1
  [[ -z $job[last_not_ready] ]] || normalize_due "$job[last_not_ready]" >/dev/null || return 1
  [[ -z $job[not_ready_checks] || $job[not_ready_checks] == <-> ]] || return 1
  normalize_due "$job[due]" >/dev/null || return 1
  [[ $job[created] == <->-<->-<->T<->:<-> ]] || return 1
  [[ "$job[kind]/$job[host]" == (/|script/|agent/mac|agent/vanpi) ]] || return 1
  # A record without kind/host is a script or Mac agent job, unless the folder says vanpi.
  [[ -n $job[kind] ]] || ! pi_evidence $dir || return 1
  normalize_due "$job[created]" >/dev/null || return 1
  [[ ${dir:t} == ${job[due]%%T*}-$job[name] ]]
}

job_trusted() {  # DIR: private folder and a valid complete record (now in $job); the run file is not checked
  private_path $1 && private_path $1/job && load_job $1 && job_record_valid $1
}

run_executable() { private_path $1/run && [[ -x $1/run ]] }  # DIR: its run file can be started

job_runnable() {  # DIR: a trusted job whose run file can be started
  job_trusted $1 && run_executable $1
}

cmd_run_now() {
  local relaunch
  relaunch_args "$@"
  acquire_mutation_lock
  resolve_job $REPLY
  local dir=$REPLY id=${REPLY:t}
  require_runnable_job $dir
  [[ $job[state] == (pending|failed) ]] || die "job $id has invalid state: $job[state]"
  if pi_job; then
    pi_rearm $dir run-now "$relaunch"
    pi_snapshot $dir || die "cannot read the Pi run state of $id"
  elif [[ -n $relaunch ]]; then
    die "--relaunch applies only to vanpi jobs"
  fi
  if pi_job && (( pi[owes] )); then pi_collect $dir manual; else run_job $dir manual; fi
  if (( run_job_collecting )); then
    print -r -- "running $id: its Pi run is still going and a later check collects it; output in $dir/last.log"
    exit $run_now_not_ready
  fi
  if (( run_job_not_ready )); then
    # Only a pending job is retried by the checker; a failed Mac job stays failed.
    print -r -- "not ready $id;${${job[state]:#failed}:+ the next check retries;} output in $dir/last.log"
    exit $run_now_not_ready
  fi
  if (( run_job_done )); then
    print -r -- "done $id"
    return 0
  fi
  print -r -- "failed $id exit $run_job_status; output in $dir/last.log"
  exit 3
}

flag_invalid() {  # DIR [WHAT]: warn once per invalid folder; the marker clears once it is valid or gone
  local marker=$flagged_dir/${1:t} what=${2:-Its directory, job record or run file is invalid}
  log_line "skipped ${1:t}: ${what:l}"
  [[ ! -e $marker ]] || return 0
  alert "Deferred job not run: ${1:t}" "$what. Fix or remove it in Mac Panel (macpanel): $1"
  : > $marker
}

cmd_run_due() {
  acquire_job_lock 0 || return 0  # another check is already running
  local now dir marker
  strftime '%F %T' $EPOCHSECONDS > $heartbeat
  for dir in $jobs_dir/*(N/); do
    # Read per job: an earlier job's pass may have held the checker past a due or give-up time.
    now=$(window_now)
    if ! job_trusted $dir; then flag_invalid $dir; continue; fi
    if pi_job; then pi_check $dir $now; continue; fi   # its deadline does not need the run file
    if ! run_executable $dir; then flag_invalid $dir; continue; fi
    /bin/rm -f -- $flagged_dir/${dir:t}
    [[ $job[state] == pending && ! $job[due] > $now ]] || continue
    run_job $dir
  done
  for marker in $flagged_dir/*(N); do
    [[ -d $jobs_dir/${marker:t} ]] || /bin/rm -f -- $marker
  done
}

awake_since() {  # epoch of the last wake, or of boot when the Mac has not slept
  local wake boot
  wake=$(/usr/sbin/sysctl -n kern.waketime 2>/dev/null) || wake=
  boot=$(/usr/sbin/sysctl -n kern.boottime 2>/dev/null) || boot=
  wake=${${wake#*sec = }%%,*}; boot=${${boot#*sec = }%%,*}
  [[ $wake == <-> ]] || wake=0; [[ $boot == <-> ]] || boot=0
  print -- $(( wake > boot ? wake : boot ))
}

cmd_watchdog() {
  local -a queued=($jobs_dir/*(N/))
  (( ${#queued} )) || return 0
  (( EPOCHSECONDS - $(awake_since) >= wake_grace )) || return 0
  checker_stale || return 0
  alert "Deferred jobs checker stopped" \
    "$label: $(checker_summary); ${#queued} job(s) are waiting. Check Mac Panel or: ${script:t} status"
}

cmd_status() {  # exits nonzero unless the agent is loaded and has checked recently
  local rc=0
  local -a queued=($jobs_dir/*(N/))
  if agent_loaded; then print -r -- "$label: loaded"; else print -r -- "$label: NOT loaded"; rc=1; fi
  checker_summary
  if checker_stale; then print -r -- "checker is stale (nothing in the last $(( stale_after / 3600 ))h)"; rc=1; fi
  print -r -- "jobs: ${#queued} in $jobs_dir"
  return $rc
}

cmd_install() {
  [[ $label == com.jacobr.deferred-jobs && $HOME == /Users/jacobr ]] || die "the plist is pinned to /Users/jacobr"
  /bin/zsh -n $script
  /usr/bin/plutil -lint -s $plist_source
  ensure_root
  /bin/mkdir -p -- ${plist_target:h}
  /usr/bin/install -m 600 -- $plist_source $plist_target
  /bin/launchctl bootout gui/$UID/$label 2>/dev/null || true
  /bin/launchctl bootstrap gui/$UID $plist_target
  agent_loaded || die "$label did not load"
  print -r -- "installed $label (hourly and at login)"
}

cmd_uninstall() {  # removes only the registration; queued jobs stay
  /bin/launchctl bootout gui/$UID/$label 2>/dev/null || true
  /bin/rm -f -- $plist_target
  print -r -- "uninstalled $label; jobs left in $jobs_dir"
}

(( $# )) || usage
verb=$1; shift
case $verb in
  add) cmd_add "$@" ;;
  add-agent) cmd_add_agent "$@" ;;
  list) cmd_list "$@" ;;
  status) cmd_status ;;
  cancel) cmd_cancel "$@" ;;
  retry) cmd_retry "$@" ;;
  run-now) cmd_run_now "$@" ;;
  reschedule) cmd_reschedule "$@" ;;
  run-due) cmd_run_due ;;
  watchdog) cmd_watchdog ;;
  install) cmd_install ;;
  uninstall) cmd_uninstall ;;
  *) usage ;;
esac
