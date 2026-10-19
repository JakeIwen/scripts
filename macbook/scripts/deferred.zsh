#!/bin/zsh
# One-off jobs that run once on or after a due time, then delete themselves.
# Each job is a private directory under $root/jobs holding an executable `run`,
# a `job` record (see job_keys) and any material the job needs; a successful
# run deletes the whole directory, and a failure keeps it and sends an ntfy
# warning. The LaunchAgent com.jacobr.deferred-jobs calls `run-due` hourly and
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
# Every job record defines exactly these keys; `state` is pending or failed.
job_keys=(name due description created origin attempts max_attempts timeout state)
typeset -A job          # the record load_job last read
typeset run_job_status=0

usage() {
  print -u2 -- "usage: ${script:t} add [--attempts N] [--timeout SECONDS] [--file PATH]... DUE NAME SCRIPT [DESCRIPTION]
       ${script:t} list [--json]
       ${script:t} cancel JOB | retry JOB | run-now JOB | reschedule JOB DUE
       ${script:t} status | run-due | watchdog | install | uninstall
DUE is YYYY-MM-DD, 'YYYY-MM-DD HH:MM' (local time), +Nd or +Nh."
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
}

save_job() {  # DIR: rewrites DIR/job from $job atomically
  local key
  local -a lines
  for key in $job_keys; do lines+=("$key=$job[$key]"); done
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

alert() {  # TITLE MESSAGE: high priority; the sender's reason, never the URL, goes to the log
  print -r -- $2 | /usr/bin/python3 -I -B $notifier --secrets $secrets --title $1 2>>$log ||
    log_line "warning undelivered: $1"
}

agent_loaded() { /bin/launchctl print gui/$UID/$label >/dev/null 2>&1 }
valid_name() { [[ $1 == [a-z0-9][a-z0-9-]# && ${#1} -le 60 ]] }

checker_summary() {
  if [[ -f $heartbeat ]]; then print -r -- "checker last ran $(<$heartbeat)"; else print -r -- "checker has never run"; fi
}

checker_stale() {  # true when the checker has not run within stale_after
  local last=0
  [[ -f $heartbeat ]] && last=$(zstat -L +mtime -- $heartbeat)
  (( EPOCHSECONDS - last > stale_after ))
}

cmd_add() {
  local attempts=$default_attempts timeout=$default_timeout file
  local -a files
  while [[ ${1-} == --* ]]; do
    case $1 in
      --attempts) [[ ${2-} == <1-> ]] || die "--attempts needs a positive number"; attempts=$2; shift 2 ;;
      --timeout) [[ ${2-} == <1-> ]] || die "--timeout needs seconds"; timeout=$2; shift 2 ;;
      --file) [[ -f ${2-} ]] || die "--file needs an existing file"; files+=(${2:A}); shift 2 ;;
      *) usage ;;
    esac
  done
  (( $# >= 3 )) || usage
  local due name=$2 src=$3 description=${${4-}//$'\n'/ }
  due=$(normalize_due $1) || die "invalid due time: $1"
  valid_name $name || die "name must be lowercase letters, digits and dashes"
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
    origin "$PWD" attempts 0 max_attempts "$attempts" timeout "$timeout" state pending)
  save_job $dir
  log_line "added ${dir:t} due $due"
  print -r -- $dir
  agent_loaded || print -u2 -- "warning: $label is not loaded, so nothing will run it; run: ${script:t} install"
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
  done
}

print_list_json() {
  emit_list_json_records | /usr/bin/python3 -I -B -c '
import json
import pathlib
import sys


def tail_log(path):
    try:
        data = pathlib.Path(path).read_bytes()
    except FileNotFoundError:
        return None
    lines = data.splitlines(keepends=True)[-64:]
    return b"".join(lines)[-8192:].decode("utf-8", errors="replace")


fields = iter(part.decode("utf-8", errors="replace")
              for part in sys.stdin.buffer.read().split(b"\0")[:-1])
if next(fields) != "H":
    raise SystemExit("invalid deferred list stream")
loaded = next(fields) == "1"
has_last_run = next(fields) == "1"
last_run = next(fields)
stale = next(fields) == "1"
result = {
    "checker": {
        "agent_loaded": loaded,
        "last_run": last_run if has_last_run else None,
        "stale": stale,
    },
    "jobs_dir": next(fields),
    "jobs": [],
}
for kind in fields:
    job_id = next(fields)
    if kind == "I":
        result["jobs"].append({"id": job_id, "valid": False})
        continue
    if kind != "V":
        raise SystemExit("invalid deferred job stream")
    result["jobs"].append({
        "id": job_id,
        "valid": True,
        "name": next(fields),
        "due": next(fields),
        "state": next(fields),
        "attempts": int(next(fields)),
        "max_attempts": int(next(fields)),
        "timeout": int(next(fields)),
        "description": next(fields),
        "created": next(fields),
        "origin": next(fields),
        "last_log": tail_log(next(fields)),
    })
json.dump(result, sys.stdout, ensure_ascii=False)
sys.stdout.write("\n")
'
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
  valid_name $1 || die "no job named $1"
  local -a matches=($jobs_dir/$1(N/) $jobs_dir/<->-<->-<->-$1(N/))
  (( ${#matches} == 1 )) || die "no single job matches $1"
  REPLY=$matches[1]
}

cmd_cancel() {
  (( $# == 1 )) || usage
  acquire_mutation_lock
  resolve_job $1
  local dir=$REPLY
  /bin/rm -rf -- $dir
  log_line "cancelled ${dir:t}"
  print -r -- "cancelled ${dir:t}"
}

require_runnable_job() {  # DIR: load it or fail with a user-facing error
  job_runnable $1 || die "job ${1:t} is not runnable (invalid record or permissions)"
}

cmd_retry() {
  (( $# == 1 )) || usage
  acquire_mutation_lock
  resolve_job $1
  local dir=$REPLY
  require_runnable_job $dir
  [[ $job[state] != pending ]] || die "job ${dir:t} is already pending"
  [[ $job[state] == failed ]] || die "job ${dir:t} has invalid state: $job[state]"
  job[state]=pending
  job[attempts]=0
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
  new_dir=$jobs_dir/${due%%T*}-$job[name]
  [[ $new_dir == $dir || ! -e $new_dir ]] || die "job ${new_dir:t} already exists"
  job[due]=$due
  if [[ $job[state] == failed ]]; then
    job[state]=pending
    job[attempts]=0
  fi
  save_job $dir
  if [[ $new_dir != $dir ]]; then /bin/mv -- $dir $new_dir; fi
  log_line "rescheduled ${dir:t} as ${new_dir:t} due $due"
  print -r -- "${new_dir:t}"
}

execute_job() {  # DIR TIMEOUT: one attempt; returns the job's exit status
  local dir=$1 timeout=$2 pid watcher rc=0
  (
    cd -- $dir
    exec /usr/bin/env -i HOME=$HOME USER=$USERNAME LOGNAME=$USERNAME LANG=en_US.UTF-8 \
      PATH=$job_path TMPDIR=${TMPDIR:-/tmp} DEFERRED_JOB_DIR=$dir ./run
  ) > $dir/last.log 2>&1 &
  pid=$!
  { sleep $timeout; kill -TERM $pid } >/dev/null 2>&1 &
  watcher=$!
  wait $pid || rc=$?
  /usr/bin/pkill -P $watcher >/dev/null 2>&1 || true
  kill $watcher >/dev/null 2>&1 || true
  return $rc
}

run_job() {  # DIR MODE: one scheduled/manual attempt; purge success, warn only scheduled
  local dir=$1 mode=${2:-scheduled} rc=0
  run_job_status=0
  job[attempts]=$(( job[attempts] + 1 ))
  save_job $dir  # recorded first, so a crash mid-run still counts
  execute_job $dir $job[timeout] || rc=$?
  run_job_status=$rc
  local tries="attempt $job[attempts]/$job[max_attempts]" prefix=
  [[ $mode == manual ]] && prefix="manual "
  if (( rc == 0 )); then
    [[ ${dir:h} == $jobs_dir && -n ${dir:t} ]] || die "refusing to delete $dir"
    /bin/rm -rf -- $dir
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
  normalize_due "$job[due]" >/dev/null || return 1
  [[ $job[created] == <->-<->-<->T<->:<-> ]] || return 1
  normalize_due "$job[created]" >/dev/null || return 1
  [[ ${dir:t} == ${job[due]%%T*}-$job[name] ]]
}

job_runnable() {  # DIR: private folder, executable run, valid complete record (now in $job)
  private_path $1 && private_path $1/job && private_path $1/run && [[ -x $1/run ]] &&
    load_job $1 && job_record_valid $1
}

cmd_run_now() {
  (( $# == 1 )) || usage
  acquire_mutation_lock
  resolve_job $1
  local dir=$REPLY id=${REPLY:t}
  require_runnable_job $dir
  [[ $job[state] == (pending|failed) ]] || die "job $id has invalid state: $job[state]"
  run_job $dir manual
  if (( run_job_status == 0 )); then
    print -r -- "done $id"
    return 0
  fi
  print -r -- "failed $id exit $run_job_status; output in $dir/last.log"
  exit 3
}

flag_invalid() {  # DIR: warn once per invalid folder; the marker clears once it is valid or gone
  local marker=$flagged_dir/${1:t}
  log_line "skipped ${1:t}: not a valid private job directory"
  [[ ! -e $marker ]] || return 0
  alert "Deferred job not run: ${1:t}" \
    "Its directory, job record or run file is invalid. Fix or remove it in Mac Panel (macpanel): $1"
  : > $marker
}

cmd_run_due() {
  acquire_job_lock 0 || return 0  # another check is already running
  local now dir marker
  now=$(strftime $stamp_format $EPOCHSECONDS)
  strftime '%F %T' $EPOCHSECONDS > $heartbeat
  for dir in $jobs_dir/*(N/); do
    if ! job_runnable $dir; then flag_invalid $dir; continue; fi
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
