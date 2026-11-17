#!/bin/bash
# Root-filesystem growth history and bounded per-path budget checks for
# backup_watchdog.sh. This file is sourced after backup_conf.sh.

rootfs_format_mib() {
  local mib=${1:?MiB value required}
  local tenths
  tenths=$(( (mib * 10 + 512) / 1024 ))
  printf '%d.%dGiB' "$((tenths / 10))" "$((tenths % 10))"
}

rootfs_add_problem() {
  problems+=("$1")
  ROOTFS_PROBLEM=1
}

rootfs_insert_measurement() {
  local path=$1 mib=$2 index previous
  index=${#ROOTFS_MEASURED_MIB[@]}
  while (( index > 0 )); do
    previous=$((index - 1))
    (( mib <= ROOTFS_MEASURED_MIB[previous] )) && break
    ROOTFS_MEASURED_MIB[index]=${ROOTFS_MEASURED_MIB[previous]}
    ROOTFS_MEASURED_PATHS[index]=${ROOTFS_MEASURED_PATHS[previous]}
    index=$previous
  done
  ROOTFS_MEASURED_MIB[index]=$mib
  ROOTFS_MEASURED_PATHS[index]=$path
}

rootfs_measure_budget_paths() {
  local entry path budget_mib output result kib measured_mib parent
  local timeout_command=${ROOTFS_TIMEOUT_COMMAND:-/usr/bin/timeout}
  local ionice_command=${ROOTFS_IONICE_COMMAND:-/usr/bin/ionice}
  local nice_command=${ROOTFS_NICE_COMMAND:-/usr/bin/nice}
  local du_command=${ROOTFS_DU_COMMAND:-/usr/bin/du}

  ROOTFS_MEASURED_MIB=()
  ROOTFS_MEASURED_PATHS=()
  ROOTFS_FAILED_PATHS=()
  ROOTFS_ABSENT_PATHS=()

  for entry in "${ROOTFS_PATH_BUDGETS_MIB[@]}"; do
    path=${entry%:*}
    budget_mib=${entry##*:}
    if [[ "$path" != /* || "$path" == "$entry" ||
          ! "$budget_mib" =~ ^[1-9][0-9]*$ ]]; then
      rootfs_add_problem "invalid rootfs path budget: $entry"
      ROOTFS_FAILED_PATHS+=("$path")
      continue
    fi

    # A budget may be declared before its writer is installed (the bounded-
    # writers rule wants the entry first). A path absent under a searchable
    # parent uses nothing, so it is listed, not a problem; anything du cannot
    # read for another reason still is.
    parent=${path%/*}
    parent=${parent:-/}
    if [[ ! -e "$path" && ! -L "$path" && -d "$parent" && -x "$parent" ]]; then
      ROOTFS_ABSENT_PATHS+=("$path")
      continue
    fi

    output=$("$timeout_command" -k 5 "${ROOTFS_DU_TIMEOUT_SECONDS}s" \
      "$ionice_command" -c3 "$nice_command" -n 19 \
      "$du_command" -skx -- "$path" 2>&1)
    result=$?
    if (( result != 0 )); then
      rootfs_add_problem "could not measure rootfs path $path (du exit $result)"
      ROOTFS_FAILED_PATHS+=("$path")
      continue
    fi

    read -r kib _ <<< "$output"
    if [[ ! "$kib" =~ ^[0-9]+$ ]]; then
      rootfs_add_problem "could not parse rootfs path size for $path"
      ROOTFS_FAILED_PATHS+=("$path")
      continue
    fi

    measured_mib=$(( (kib + 1023) / 1024 ))
    rootfs_insert_measurement "$path" "$measured_mib"
    if (( measured_mib > budget_mib )); then
      rootfs_add_problem "rootfs path $path uses $(rootfs_format_mib "$measured_mib") (budget $(rootfs_format_mib "$budget_mib"))"
    fi
  done
}

rootfs_read_used_mib() {
  local output result line used avail extra
  local df_command=${ROOTFS_DF_COMMAND:-/bin/df}

  local parsed=0
  ROOTFS_USED_MIB=0
  ROOTFS_AVAIL_MIB=0
  ROOTFS_USED_KIB=0
  ROOTFS_AVAIL_KIB=0
  output=$("$df_command" -k --output=used,avail / 2>&1)
  result=$?
  if (( result != 0 )); then
    rootfs_add_problem "could not measure rootfs usage (df exit $result)"
    return 1
  fi
  while read -r used avail extra; do
    # avail goes negative when root has eaten the reserved blocks, which is
    # exactly when the checks matter most.
    if [[ "$used" =~ ^[0-9]+$ && "$avail" =~ ^-?[0-9]+$ && -z "${extra:-}" ]]; then
      ROOTFS_USED_KIB=$used
      ROOTFS_AVAIL_KIB=$avail
      ROOTFS_USED_MIB=$((used / 1024))
      ROOTFS_AVAIL_MIB=$((avail / 1024))
      parsed=1
    fi
  done <<< "$output"
  if (( ! parsed )); then
    rootfs_add_problem "could not parse rootfs usage"
    return 1
  fi
}

rootfs_load_history() {
  local history_file=$1 current_day=$2
  local day epoch used extra previous_day=-1

  ROOTFS_HISTORY_DAYS=()
  ROOTFS_HISTORY_EPOCHS=()
  ROOTFS_HISTORY_USED_MIB=()
  [[ -e "$history_file" ]] || return 0
  if [[ -L "$history_file" || ! -f "$history_file" ]]; then
    rootfs_add_problem "rootfs usage history is not a safe regular file: $history_file"
    return 1
  fi
  if [[ ! -r "$history_file" ]]; then
    rootfs_add_problem "rootfs usage history is unreadable: $history_file"
    return 1
  fi

  while read -r day epoch used extra || [[ -n "${day:-}${epoch:-}${used:-}${extra:-}" ]]; do
    if [[ ! "$day" =~ ^[0-9]+$ || ! "$epoch" =~ ^[0-9]+$ ||
          ! "$used" =~ ^[0-9]+$ || -n "${extra:-}" ||
          "$day" -lt "$previous_day" || "$day" -gt "$current_day" ]]; then
      rootfs_add_problem "rootfs usage history is malformed: $history_file"
      return 1
    fi
    if (( day == previous_day )); then
      ROOTFS_HISTORY_EPOCHS[${#ROOTFS_HISTORY_EPOCHS[@]}-1]=$epoch
      ROOTFS_HISTORY_USED_MIB[${#ROOTFS_HISTORY_USED_MIB[@]}-1]=$used
    else
      ROOTFS_HISTORY_DAYS+=("$day")
      ROOTFS_HISTORY_EPOCHS+=("$epoch")
      ROOTFS_HISTORY_USED_MIB+=("$used")
      previous_day=$day
    fi
  done < "$history_file"
}

rootfs_write_history() {
  local history_file=$1 max_entries=$2
  local count start index temp_file

  count=${#ROOTFS_HISTORY_DAYS[@]}
  start=$((count > max_entries ? count - max_entries : 0))
  temp_file=$(/usr/bin/mktemp "${history_file}.tmp.XXXXXX") || return 1
  if ! {
    index=$start
    while (( index < count )); do
      printf '%s %s %s\n' \
        "${ROOTFS_HISTORY_DAYS[index]}" \
        "${ROOTFS_HISTORY_EPOCHS[index]}" \
        "${ROOTFS_HISTORY_USED_MIB[index]}"
      index=$((index + 1))
    done
  } > "$temp_file"; then
    /bin/rm -f -- "$temp_file"
    return 1
  fi
  /bin/chmod 0600 "$temp_file" || {
    /bin/rm -f -- "$temp_file"
    return 1
  }
  /bin/mv -f -- "$temp_file" "$history_file" || {
    /bin/rm -f -- "$temp_file"
    return 1
  }
}

rootfs_check_growth() {
  local now=$1 current_day history_file max_entries index last_index
  local cutoff baseline_epoch= baseline_used= elapsed_seconds growth_mib threshold_mib

  current_day=$((now / 86400))
  history_file=${ROOTFS_HISTORY_FILE:-"$STAMP_DIR/rootfs_used_mib.history"}
  max_entries=${ROOTFS_HISTORY_MAX_ENTRIES:-120}
  if [[ ! "$max_entries" =~ ^[1-9][0-9]*$ ]]; then
    rootfs_add_problem "invalid rootfs history limit: $max_entries"
    return
  fi
  if ! rootfs_load_history "$history_file" "$current_day"; then
    return
  fi

  cutoff=$((now - 7 * 86400))
  index=0
  while (( index < ${#ROOTFS_HISTORY_EPOCHS[@]} )); do
    if (( ROOTFS_HISTORY_EPOCHS[index] <= cutoff )); then
      baseline_epoch=${ROOTFS_HISTORY_EPOCHS[index]}
      baseline_used=${ROOTFS_HISTORY_USED_MIB[index]}
    fi
    index=$((index + 1))
  done

  last_index=$((${#ROOTFS_HISTORY_DAYS[@]} - 1))
  if (( last_index >= 0 )); then
    if (( ROOTFS_HISTORY_DAYS[last_index] == current_day )); then
      ROOTFS_HISTORY_EPOCHS[last_index]=$now
      ROOTFS_HISTORY_USED_MIB[last_index]=$ROOTFS_USED_MIB
    else
      ROOTFS_HISTORY_DAYS+=("$current_day")
      ROOTFS_HISTORY_EPOCHS+=("$now")
      ROOTFS_HISTORY_USED_MIB+=("$ROOTFS_USED_MIB")
    fi
  else
    ROOTFS_HISTORY_DAYS+=("$current_day")
    ROOTFS_HISTORY_EPOCHS+=("$now")
    ROOTFS_HISTORY_USED_MIB+=("$ROOTFS_USED_MIB")
  fi

  ROOTFS_HISTORY_OK=1
  if ! rootfs_write_history "$history_file" "$max_entries"; then
    rootfs_add_problem "could not atomically update rootfs usage history: $history_file"
  fi

  if [[ -n "$baseline_epoch" ]]; then
    elapsed_seconds=$((now - baseline_epoch))
    growth_mib=$(( (ROOTFS_USED_MIB - baseline_used) * 7 * 86400 / elapsed_seconds ))
    threshold_mib=$((ROOT_GROWTH_WARN_GIB_7D * 1024))
    if (( growth_mib > threshold_mib )); then
      rootfs_add_problem "rootfs grew $(rootfs_format_mib "$growth_mib") in the last 7d (limit ${ROOT_GROWTH_WARN_GIB_7D}GiB/7d)"
    fi
  fi
}

# Replace a file atomically with stdin (mode 0600).
rootfs_replace_file() {
  local file=$1 temp_file
  temp_file=$(/usr/bin/mktemp "${file}.tmp.XXXXXX") || return 1
  if ! cat > "$temp_file" || ! /bin/chmod 0600 "$temp_file" ||
     ! /bin/mv -f -- "$temp_file" "$file"; then
    /bin/rm -f -- "$temp_file"
    return 1
  fi
}

# One line per UTC day: "<day> <epoch> <path>:<MiB> ..." for every path
# measured successfully today. A same-day line is replaced; lines older than
# ROOTFS_PATH_HISTORY_DAYS are dropped.
rootfs_record_path_history() {
  local now=$1 file keep_days current_day line day index kept=() entry
  file=${ROOTFS_PATH_HISTORY_FILE:-"$STAMP_DIR/rootfs_paths.history"}
  keep_days=${ROOTFS_PATH_HISTORY_DAYS:-400}
  current_day=$((now / 86400))
  (( ${#ROOTFS_MEASURED_PATHS[@]} > 0 )) || return 0
  if [[ -L "$file" || ( -e "$file" && ( ! -f "$file" || ! -r "$file" ) ) ]]; then
    rootfs_add_problem "rootfs path history is not a safe readable file: $file"
    return 1
  fi
  if [[ -f "$file" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
      day=${line%% *}
      [[ "$day" =~ ^[0-9]+$ ]] || continue
      (( day == current_day || day < current_day - keep_days )) && continue
      kept+=("$line")
    done < "$file"
  fi
  entry="$current_day $now"
  index=0
  while (( index < ${#ROOTFS_MEASURED_PATHS[@]} )); do
    entry+=" ${ROOTFS_MEASURED_PATHS[index]}:${ROOTFS_MEASURED_MIB[index]}"
    index=$((index + 1))
  done
  kept+=("$entry")
  if ! printf '%s\n' "${kept[@]}" | rootfs_replace_file "$file"; then
    rootfs_add_problem "could not atomically update rootfs path history: $file"
  fi
}

# --- Level and forecast alerts -------------------------------------------
# State file lines: level70=0|1 level80=.. level90=.. forecast=0|1. A level is
# 1 from the run that alerted on it until usage falls ROOTFS_ALERT_HYSTERESIS
# points below it. Nothing is marked alerted unless the send succeeded, so a
# failed send is retried by the next watchdog run.

rootfs_load_alert_state() {
  local file=$1 key value
  ROOTFS_ALERT_L70=0 ROOTFS_ALERT_L80=0 ROOTFS_ALERT_L90=0 ROOTFS_ALERT_FORECAST=0
  [[ -e "$file" ]] || return 0
  if [[ -L "$file" || ! -f "$file" || ! -r "$file" ]]; then
    rootfs_add_problem "rootfs alert state is not a safe readable file: $file"
    return 1
  fi
  while IFS='=' read -r key value || [[ -n "$key" ]]; do
    [[ "$value" == 0 || "$value" == 1 ]] || continue
    case "$key" in
      level70) ROOTFS_ALERT_L70=$value ;;
      level80) ROOTFS_ALERT_L80=$value ;;
      level90) ROOTFS_ALERT_L90=$value ;;
      forecast) ROOTFS_ALERT_FORECAST=$value ;;
    esac
  done < "$file"
}

rootfs_write_alert_state() {
  local file=$1
  if ! printf 'level70=%s\nlevel80=%s\nlevel90=%s\nforecast=%s\n' \
      "$ROOTFS_ALERT_L70" "$ROOTFS_ALERT_L80" "$ROOTFS_ALERT_L90" \
      "$ROOTFS_ALERT_FORECAST" | rootfs_replace_file "$file"; then
    rootfs_add_problem "could not atomically update rootfs alert state: $file"
  fi
}

# Alerts go through the watchdog's notify function, on the warnings topic.
rootfs_send_alert() {
  local title=$1 message=$2 priority=$3 tags=$4
  NTFY_TOPIC_VAR=${ROOTFS_NTFY_TOPIC_VAR:-NTFY_WARNING_URL} \
    notify "$title" "$message" "$priority" "$tags"
}

rootfs_format_date() {
  local epoch=$1
  /bin/date -u -d "@$epoch" +%F 2>/dev/null || /bin/date -u -r "$epoch" +%F
}

# Least-squares slope of used MiB over the last ROOTFS_FORECAST_WINDOW_DAYS,
# projected from the current reading to the 90% line. Sets
# ROOTFS_FORECAST_DAYS (whole days; empty when there is no rising trend or too
# few points) and ROOTFS_FORECAST_EPOCH.
rootfs_forecast() {
  local now=$1 total_mib=$2 window min_points result index
  window=${ROOTFS_FORECAST_WINDOW_DAYS:-30}
  min_points=${ROOTFS_FORECAST_MIN_POINTS:-7}
  ROOTFS_FORECAST_DAYS=
  ROOTFS_FORECAST_EPOCH=
  [[ "${ROOTFS_HISTORY_OK:-0}" == 1 ]] || return 0
  result=$(
    index=0
    while (( index < ${#ROOTFS_HISTORY_EPOCHS[@]} )); do
      printf '%s %s\n' "${ROOTFS_HISTORY_EPOCHS[index]}" "${ROOTFS_HISTORY_USED_MIB[index]}"
      index=$((index + 1))
    done | /usr/bin/awk -v now="$now" -v window="$window" -v minpts="$min_points" \
      -v cur="$ROOTFS_USED_MIB" -v target="$((total_mib * 90 / 100))" '
      $1 >= now - window * 86400 {
        x = ($1 - now) / 86400; n++; sx += x; sy += $2; sxx += x * x; sxy += x * $2
      }
      END {
        if (n < minpts) exit
        den = n * sxx - sx * sx
        if (den <= 0) exit
        slope = (n * sxy - sx * sy) / den
        if (slope <= 0) exit
        days = (target - cur) / slope
        if (days < 0) days = 0
        printf "%d %d\n", days, now + days * 86400
      }'
  )
  read -r ROOTFS_FORECAST_DAYS ROOTFS_FORECAST_EPOCH <<< "$result"
}

rootfs_check_alerts_locked() {
  local now=$1 percent state_file level var newly="" highest=0 changed=0
  local total_kib total_mib horizon clear_at forecast_note priority tags message test_note=""
  state_file=${ROOTFS_ALERT_STATE_FILE:-"$STAMP_DIR/rootfs_alert.state"}
  total_kib=$((ROOTFS_USED_KIB + ROOTFS_AVAIL_KIB))
  total_mib=$((total_kib / 1024))
  # Percent as df prints it: used / (used + avail) from the raw KiB counters,
  # rounded up; a non-positive total (no space left at all) counts as full.
  if (( total_kib > 0 )); then
    percent=$(( (ROOTFS_USED_KIB * 100 + total_kib - 1) / total_kib ))
  else
    percent=100
  fi
  if [[ -n "${ROOTFS_TEST_PERCENT:-}" ]]; then
    if [[ ! "$ROOTFS_TEST_PERCENT" =~ ^[0-9]{1,3}$ ]]; then
      rootfs_add_problem "invalid ROOTFS_TEST_PERCENT: $ROOTFS_TEST_PERCENT"
      return
    fi
    percent=$ROOTFS_TEST_PERCENT
    test_note=" (forced by ROOTFS_TEST_PERCENT)"
  fi
  ROOTFS_PERCENT=$percent
  rootfs_load_alert_state "$state_file" || return

  for level in 70 80 90; do
    var=ROOTFS_ALERT_L$level
    if (( ${!var} )) && (( percent <= level - ${ROOTFS_ALERT_HYSTERESIS:-5} )); then
      printf -v "$var" 0
      changed=1
    elif (( ! ${!var} )) && (( percent >= level )); then
      newly+=" $level"
      highest=$level
    fi
  done
  if (( highest > 0 )); then
    case "$highest" in
      70) priority=default tags=warning ;;
      80) priority=high tags=warning ;;
      *)  priority=urgent tags=rotating_light ;;
    esac
    message="Root filesystem is ${percent}% full (crossed ${highest}%)$test_note: $(rootfs_format_mib "$ROOTFS_USED_MIB") used, $(rootfs_format_mib $((ROOTFS_AVAIL_MIB > 0 ? ROOTFS_AVAIL_MIB : 0))) free of $(rootfs_format_mib "$total_mib"). Next alerts at 80% and 90%; each re-arms once usage is 5 points below its level."
    if rootfs_send_alert "vanpi root disk ${percent}%" "$message" "$priority" "$tags"; then
      for level in $newly; do printf -v "ROOTFS_ALERT_L$level" 1; done
      changed=1
    else
      echo "rootfs level alert not delivered; will retry on the next run" >&2
    fi
  fi

  # Forecast: fires when 90% is projected within the horizon and re-arms once
  # the projection is ROOTFS_FORECAST_CLEAR_DAYS away (or the trend is gone).
  # A reading already at 90% is covered by the level alert.
  horizon=${ROOTFS_FORECAST_HORIZON_DAYS:-30}
  clear_at=${ROOTFS_FORECAST_CLEAR_DAYS:-35}
  rootfs_forecast "$now" "$total_mib"
  if (( percent >= 90 )); then
    :
  elif [[ -n "$ROOTFS_FORECAST_DAYS" ]] && (( ROOTFS_FORECAST_DAYS < horizon )); then
    if (( ! ROOTFS_ALERT_FORECAST )); then
      message="Root filesystem is ${percent}% full and growing: at the ${ROOTFS_FORECAST_WINDOW_DAYS:-30}-day trend it reaches 90% on $(rootfs_format_date "$ROOTFS_FORECAST_EPOCH") (in ${ROOTFS_FORECAST_DAYS} days)."
      if rootfs_send_alert "vanpi root disk fills by $(rootfs_format_date "$ROOTFS_FORECAST_EPOCH")" "$message" high warning; then
        ROOTFS_ALERT_FORECAST=1
        changed=1
      else
        echo "rootfs forecast alert not delivered; will retry on the next run" >&2
      fi
    fi
  elif (( ROOTFS_ALERT_FORECAST )) &&
       { [[ -z "$ROOTFS_FORECAST_DAYS" ]] || (( ROOTFS_FORECAST_DAYS >= clear_at )); }; then
    ROOTFS_ALERT_FORECAST=0
    changed=1
  fi
  (( changed )) && rootfs_write_alert_state "$state_file"
  return 0
}

# Boot, scheduled and manual runs can overlap; the read-send-write of the alert
# state is one transaction under a non-blocking flock on a file beside it (fd 8).
# A run that finds it held skips the alerts and logs; the holder sends them.
rootfs_check_alerts() {
  local now=$1 state_file lock_file flock_command
  state_file=${ROOTFS_ALERT_STATE_FILE:-"$STAMP_DIR/rootfs_alert.state"}
  lock_file=${ROOTFS_ALERT_LOCK_FILE:-"${state_file}.lock"}
  flock_command=${ROOTFS_FLOCK_COMMAND:-/usr/bin/flock}
  if [[ ! -x "$flock_command" ]]; then
    # Linux always has flock; only a development Mac lacks it.
    rootfs_check_alerts_locked "$now"
    return
  fi
  if ! exec 8>>"$lock_file"; then
    rootfs_add_problem "could not open rootfs alert lock: $lock_file"
    return
  fi
  if ! "$flock_command" -n 8; then
    echo "rootfs alert check skipped: another run holds $lock_file" >&2
    exec 8>&-
    return
  fi
  rootfs_check_alerts_locked "$now"
  exec 8>&-
}

rootfs_append_context() {
  local index path context="rootfs paths, largest first:"
  (( ROOTFS_PROBLEM )) || return 0

  index=0
  while (( index < ${#ROOTFS_MEASURED_MIB[@]} )); do
    context+=" ${ROOTFS_MEASURED_PATHS[index]}=$(rootfs_format_mib "${ROOTFS_MEASURED_MIB[index]}");"
    index=$((index + 1))
  done
  index=0
  while (( index < ${#ROOTFS_FAILED_PATHS[@]} )); do
    path=${ROOTFS_FAILED_PATHS[index]}
    context+=" $path=measurement failed;"
    index=$((index + 1))
  done
  index=0
  while (( index < ${#ROOTFS_ABSENT_PATHS[@]} )); do
    context+=" ${ROOTFS_ABSENT_PATHS[index]}=absent;"
    index=$((index + 1))
  done
  problems+=("${context%;}")
}

check_rootfs_budget() {
  local now=${1:?epoch required} used_gib
  ROOTFS_PROBLEM=0
  ROOTFS_USED_MIB=0
  ROOTFS_AVAIL_MIB=0
  ROOTFS_HISTORY_OK=0

  rootfs_measure_budget_paths
  rootfs_record_path_history "$now"
  if rootfs_read_used_mib; then
    used_gib=$((ROOTFS_USED_MIB / 1024))
    status+="rootfs: ${used_gib}GiB used. "
    if (( used_gib > ROOT_USED_MAX_GB )); then
      rootfs_add_problem "rootfs uses ${used_gib}GiB (limit ${ROOT_USED_MAX_GB}GiB for configured ${CLONE_CARD_NOMINAL_GB}GB clone cards)"
    fi
    rootfs_check_growth "$now"
    rootfs_check_alerts "$now"
  else
    status+="rootfs: measurement failed. "
  fi
  rootfs_append_context
}
