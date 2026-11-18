# Sourced by deferred.zsh: how the checker, run-now, retry and cancel treat an
# `add-agent --host vanpi` job. A Pi run outlives the pass that started it, so
# launching and collecting are separate duties: a job may launch only while it
# is pending, due and inside its give-up window, but a job that owns a run
# (a launch in flight, a run without a terminal record, or a saved report not
# yet delivered) gets a collect-only pass at every check whatever its state,
# due time or give-up time. A collect-only pass never launches and spends no
# attempt. The Pi state is read by the repository's deferred_pi_control.py,
# never by running anything from the job directory.

typeset -A pi   # the last pi_snapshot: state run token owes failure_attempt failure_kind

pi_job() { [[ $job[kind] == agent && $job[host] == vanpi ]] }  # of the record load_job read

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

pi_still_collecting() {  # DIR RC: after a failed pass, the job still owns an unfinished run
  (( $2 != 0 )) || return 1
  # Unreadable state right after a pass is never taken as a terminal failure.
  pi_snapshot $1 || return 0
  (( pi[owes] )) && [[ $pi[state] != finished ]]
}

pi_note_collecting() {  # DIR MODE: the pass left a run on the Pi to collect later
  run_job_collecting=1
  clear_not_ready
  save_job $1
  log_line "${${2:#scheduled}:+manual }collecting ${1:t}: Pi run ${pi[run]:-with token $pi[token]} is checked again at the next check"
}

pi_collect() {  # DIR MODE: one collect-only pass; ends the job, or leaves it as it was
  local dir=$1 mode=$2 rc=0 prefix=
  [[ $mode == manual ]] && prefix="manual "
  run_job_status=0 run_job_not_ready=0 run_job_collecting=0
  execute_job $dir $job[timeout] collect || rc=$?
  run_job_status=$rc
  if (( rc == 0 )); then
    [[ ${dir:h} == $jobs_dir && -n ${dir:t} ]] || die "refusing to delete $dir"
    /bin/rm -rf -- $dir
    log_line "${prefix}done ${dir:t} (collected from vanpi)"
    return 0
  fi
  if pi_still_collecting $dir $rc; then pi_note_collecting $dir $mode; return 0; fi
  (( rc != not_ready_status )) || { log_line "${prefix}nothing to collect for ${dir:t}"; return 0 }
  pi_account_failure $dir $mode $rc
}

pi_account_failure() {  # DIR MODE RC: the attempt's run ended badly (or its report was not sent)
  local dir=$1 mode=$2 rc=$3 tries="attempt $job[attempts]/$job[max_attempts]"
  log_line "${${mode:#scheduled}:+manual }failed ${dir:t} exit $rc ($tries); output in $dir/last.log"
  [[ $job[state] != failed ]] || return 0
  if (( job[attempts] >= job[max_attempts] )) || [[ $pi[failure_kind] == no-report ]]; then
    job[state]=failed
    save_job $dir
    [[ $mode != scheduled ]] || alert "Deferred job gave up: ${dir:t}" \
      "Its vanpi run ended badly on $tries; it will not retry. Job kept at $dir (see last.log); review it in Mac Panel (macpanel)."
  elif [[ $mode == scheduled ]]; then
    alert "Deferred job failed: ${dir:t}" \
      "Its vanpi run ended badly on $tries; a new run starts at the next hourly check. Review $dir/last.log in Mac Panel (macpanel)."
  fi
}

pi_failure_unaccounted() {  # the recorded failure belongs to the counted attempt and nothing may follow it
  [[ $pi[state] == failed && -n $pi[failure_attempt] && $pi[failure_attempt] == $job[attempts] ]] || return 1
  (( job[attempts] >= job[max_attempts] )) || [[ $pi[failure_kind] == no-report ]]
}

pi_run_due() {  # DIR NOW: the checker's turn for a vanpi job whose $pi is fresh
  local dir=$1 now=$2
  if (( pi[owes] )); then pi_collect $dir scheduled; return 0; fi
  [[ $job[state] == pending && ! $job[due] > $now ]] || return 0
  if pi_failure_unaccounted; then
    # A crash came between the run's failure and the scheduler saving it: finish that accounting.
    log_line "accounting the recorded failure of ${dir:t} (attempt $job[attempts]); no new run"
    pi_account_failure $dir scheduled 1
    return 0
  fi
  run_job $dir
}

pi_relaunch_consent() {  # DIR VERB TOKEN: retry/run-now may clear a launch-unknown hold only for its token
  local rc=0
  pi_control_run clear-hold $1 ${3:--} $2 || rc=$?
  (( rc == 0 )) && return 0
  (( rc == relaunch_refused_status )) && exit $relaunch_refused_status
  die "could not check the Pi launch of ${1:t}"
}

pi_cancel() {  # DIR ABANDON: stop or find finished any Pi run first; --abandon always proceeds
  local rc=0
  pi_control_run cancel ${2:+--abandon} $1 || rc=$?
  (( rc == 0 )) && return 0
  if [[ -n ${2-} ]]; then
    print -u2 -- "${script:t}: the Pi state of ${1:t} could not be read in full; deleting it anyway (--abandon)"
    return 0
  fi
  print -u2 -- "${script:t}: kept ${1:t}"
  exit $refused_status
}
