# Sourced by deferred.zsh: how the checker, run-now, retry, reschedule and
# cancel treat an `add-agent --host vanpi` job. A Pi run outlives the pass that
# started it, so launching and collecting are separate duties.
#
# A job launches by itself at most once: only while its Pi state is unlaunched,
# it is pending and due, and its give-up time has not passed. A job that owns a
# run (a launch in flight, a run without a terminal record, or a saved report
# not yet delivered) gets a collect-only pass at every check whatever its state,
# due time or give-up time; that pass never launches and spends no attempt.
# Every other outcome leaves the job failed (or held) until a person runs
# retry or run-now; when the attempt they replace is not known to be over on
# the Pi, they need --relaunch naming it (deferred_pi_control.py rearm).
#
# The scheduler, not the job's copied run script, owns the collection deadline
# and the alerts, so both hold when that copy cannot run. The Pi state is read
# by the repository's deferred_pi_control.py, never by running anything from
# the job directory.

typeset -A pi   # the last pi_snapshot: state run token owes unresolved delivered failure_kind
pi_alerts=alerts.sent   # in the job directory: one "KIND ATTEMPT" line per alert already sent
# deferred_pi_state.STATE_RECORDS: a record without kind/host beside any of them is an untrusted vanpi job.
pi_state_records=(remote-run.json launching.json launch-unknown.json remote-failed.json report-checks.json)
pi_launcher=$HOME/.claude/skills/pi-agent/pi_agent.py   # deferred_pi_remote.LAUNCHER, for alert text

pi_job() { [[ $job[kind] == agent && $job[host] == vanpi ]] }  # of the record load_job read

pi_evidence() {  # DIR: the folder shows signs of a vanpi job: Pi state records or a vanpi agent.env
  local -a found=($1/${^pi_state_records}(N))
  (( ${#found} )) && return 0
  agent_setting $1 agent_host
  [[ $REPLY == vanpi ]]
}

pi_control_run() {  # VERB ARGS...: the repository's control script, logging to the scheduler's log
  DEFERRED_LOG=$log /usr/bin/python3 -I -B $pi_control "$@"
}

pi_snapshot() {  # DIR: fills $pi from the job's state files; fails when they cannot be read
  local out line
  out=$(pi_control_run describe $1 2>/dev/null) || return 1
  pi=()
  for line in "${(@f)out}"; do pi[${line%%=*}]=${line#*=}; done
  [[ -n ${pi[state]-} ]]
}

pi_attempt() { print -r -- "${pi[run]:-$pi[token]}" }  # the attempt $pi describes

pi_recovery() {  # how to check and stop the attempt by hand
  if [[ -n $pi[run] ]]; then
    print -r -- "Check it: /usr/bin/python3 $pi_launcher status $pi[run]; stop it: /usr/bin/python3 $pi_launcher stop $pi[run]."
  else
    print -r -- "Look for it: /usr/bin/python3 $pi_launcher status -n 100000 | grep -e -$pi[token]; stop what it finds."
  fi
}

pi_alert_once() {  # DIR KIND TITLE MESSAGE: one alert per kind and attempt, whatever came before
  local file=$1/$pi_alerts key="$2 $(pi_attempt)"
  local -a sent
  [[ -f $file ]] && sent=("${(@f)$(<$file)}")
  (( ${sent[(Ie)$key]} )) && return 0
  print -r -- $key >> $file
  alert $3 $4
}

pi_collect() {  # DIR MODE: one collect-only pass
  local rc=0
  run_job_status=0 run_job_not_ready=0 run_job_collecting=0 run_job_done=0
  execute_job $1 $job[timeout] collect || rc=$?
  run_job_status=$rc
  pi_settle $1 $2 $rc collect
}

pi_settle() {  # DIR MODE RC [collect]: after a pass, end the job, keep collecting, or fail it
  # The durable state decides, never the exit status: a truncated or replaced `run`
  # can exit 0 while the Pi run is still going.
  local dir=$1 mode=$2 rc=$3 prefix=
  [[ $mode == manual ]] && prefix="manual "
  if ! pi_snapshot $dir; then
    log_line "${prefix}kept ${dir:t}: exit $rc and its vanpi run state cannot be read"
    [[ $mode != scheduled ]] || flag_invalid $dir "Its vanpi run state cannot be read (cancel --abandon removes it)"
    return 0
  fi
  if [[ $pi[state] == finished ]] && (( pi[delivered] )); then
    [[ ${dir:h} == $jobs_dir && -n ${dir:t} ]] || die "refusing to delete $dir"
    /bin/rm -rf -- $dir
    run_job_done=1
    log_line "${prefix}done ${dir:t} (vanpi)"
    return 0
  fi
  case $pi[state] in
    running|launching) pi_owned $dir $mode $rc ;;
    finished) pi_undelivered $dir $mode $rc ;;
    failed|launch-unknown) pi_ended $dir $mode ;;
    *) pi_unlaunched $dir $mode $rc ${4-} ;;
  esac
}

pi_unlaunched() {  # DIR MODE RC [collect]: the pass left nothing on record
  local dir=$1 mode=$2 rc=$3
  if (( rc == 0 )); then
    # Success with nothing launched and nothing delivered: the job's `run` is broken.
    job[state]=failed
    clear_not_ready
    save_job $dir
    log_line "${${mode:#scheduled}:+manual }collector failed ${dir:t}: exit 0 with nothing on record for vanpi; output in $dir/last.log"
    [[ $mode != scheduled ]] || alert "Deferred job collector failed: ${dir:t}" \
      "Its vanpi pass exited 0 but recorded no launch and delivered no report (see $dir/last.log). Nothing starts a run until you retry or run-now it in Mac Panel (macpanel)."
  elif [[ -n ${4-} ]]; then
    log_line "${${mode:#scheduled}:+manual }nothing to collect for ${dir:t}"
  else
    account_pass $dir $mode $rc   # not ready, or a launch refused before the Pi
  fi
}

pi_owned() {  # DIR MODE RC: the job still owns a Pi run after this pass
  local dir=$1 mode=$2 rc=$3
  if (( rc != not_ready_status )); then
    log_line "${${mode:#scheduled}:+manual }collector failed ${dir:t} exit $rc on Pi run $(pi_attempt); output in $dir/last.log"
    [[ $mode != scheduled ]] || pi_alert_once $dir collector "Deferred job collector failed: ${dir:t}" \
      "Its vanpi pass exited $rc without settling Pi run $(pi_attempt) (see $dir/last.log). The job keeps the run and every check tries again until the collection deadline. $(pi_recovery)"
  fi
  (( rc == not_ready_status )) || return 0
  run_job_collecting=1
  clear_not_ready
  save_job $dir
  log_line "${${mode:#scheduled}:+manual }collecting ${dir:t}: Pi run $(pi_attempt) is checked again at the next check"
}

pi_expire() {  # DIR: records the collection deadline's failure once it has passed (true then); refreshes $pi
  agent_setting $1 agent_run_timeout
  pi_control_run expire $1 ${${REPLY:#^<1->}:-} >> $1/last.log 2>&1 || return 1
  pi_snapshot $1 || true
}

pi_undelivered() {  # DIR MODE RC: the run finished and its report is saved, but no delivery is recorded
  local dir=$1 mode=$2 rc=$3
  log_line "${${mode:#scheduled}:+manual }undelivered ${dir:t}: the report of Pi run $pi[run] is saved; the pass exited $rc without recording its delivery, and every check sends it again"
  [[ $mode != scheduled ]] || pi_alert_once $dir delivery "Deferred job result not delivered: ${dir:t}" \
    "Its vanpi run $pi[run] finished and the report is saved in $dir, but the pass exited $rc without recording its delivery. Every check sends the saved report again; nothing runs on the Pi again."
}

pi_ended() {  # DIR MODE: the attempt ended badly, or its launch is unknown; the job waits for a person
  local dir=$1 mode=$2 what
  job[state]=failed
  clear_not_ready
  save_job $dir
  if [[ $pi[state] == launch-unknown ]]; then
    what="Its launch with token $pi[token] could not be found on vanpi, so it may be running there"
  else
    what="Its vanpi run $(pi_attempt) ended badly ($pi[failure_kind])"
  fi
  log_line "${${mode:#scheduled}:+manual }failed ${dir:t}: ${what:l}; output in $dir/last.log"
  [[ $mode != scheduled ]] || pi_alert_once $dir ended "Deferred job failed: ${dir:t}" \
    "$what. Nothing starts another run until you retry or run-now (deferred.zsh list shows whether --relaunch is needed). $(pi_recovery) Job kept at $dir (see last.log); review it in Mac Panel (macpanel)."
}

pi_window_closed() {  # DIR: an unlaunched job past its give-up time fails without launching
  local give_up=$(give_up_stamp)
  job[state]=failed
  clear_not_ready
  save_job $1
  log_line "failed ${1:t}: its give-up time $give_up passed before it launched on vanpi; nothing was started"
  alert "Deferred job gave up: ${1:t}" \
    "Its give-up time ${give_up/T/ } passed before it could launch on vanpi, so nothing was started. Job kept at $1; retry or run-now it in Mac Panel (macpanel)."
}

pi_check() {  # DIR NOW: the checker's turn for a vanpi job whose record is trusted
  local dir=$1
  if ! pi_snapshot $dir; then
    flag_invalid $dir "Its vanpi run state cannot be read (cancel --abandon removes it)"
    return 0
  fi
  # The collection deadline needs only the record and the state, never the job's run file.
  if (( pi[owes] )) && pi_expire $dir; then pi_ended $dir scheduled; return 0; fi
  ! run_executable $dir || /bin/rm -f -- $flagged_dir/${dir:t}
  pi_run_due $dir $2
}

pi_run_due() {  # DIR NOW: what the checker does for a vanpi job whose $pi is fresh
  local dir=$1 now=$2
  case $pi[state] in
    running|launching|finished) pi_passable $dir && pi_collect $dir scheduled ;;
    # Ended before the scheduler saved it (a crash between the two writes): account it once, start nothing.
    failed|launch-unknown) [[ $job[state] == failed ]] || pi_ended $dir scheduled ;;
    unlaunched)
      [[ $job[state] == pending && ! $job[due] > $now ]] || return 0
      if [[ ! $now < $(give_up_stamp) ]]; then pi_window_closed $dir; return 0; fi
      pi_passable $dir && run_job $dir ;;
  esac
  return 0
}

pi_passable() {  # DIR: its run file can make a pass; otherwise warns once and fails
  run_executable $1 && return 0
  if (( pi[owes] )); then
    log_line "collector failed ${1:t}: its run file is missing or not executable, so Pi run $(pi_attempt) is not collected"
    pi_alert_once $1 collector "Deferred job collector failed: ${1:t}" \
      "Its run file is missing or not executable, so vanpi run $(pi_attempt) is not collected; the collection deadline still holds. $(pi_recovery) Job kept at $1; review it in Mac Panel (macpanel)."
  else
    flag_invalid $1
  fi
  return 1
}

pi_rearm() {  # DIR VERB ID: retry/run-now may go ahead; relays the control script's refusal (exit 7)
  local rc=0
  pi_control_run rearm $1 $2 ${3:+"$3"} || rc=$?
  (( rc == 0 )) && return 0
  (( rc == relaunch_refused_status )) && exit $relaunch_refused_status
  die "could not check the Pi attempt of ${1:t}"
}

pi_cancel() {  # DIR ABANDON [UNTRUSTED]: stop or find finished any Pi run first; --abandon always proceeds
  local rc=0
  local -a mode
  if [[ -n ${2-} ]]; then mode=(--abandon); elif [[ -n ${3-} ]]; then mode=(--untrusted); fi
  pi_control_run cancel $mode $1 || rc=$?
  (( rc == 0 )) && return 0
  if [[ -n ${2-} ]]; then
    print -u2 -- "${script:t}: the Pi state of ${1:t} could not be read in full; deleting it anyway (--abandon)"
    return 0
  fi
  print -u2 -- "${script:t}: kept ${1:t}"
  exit $refused_status
}
