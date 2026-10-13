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
  local entry path budget_mib output result kib measured_mib
  local timeout_command=${ROOTFS_TIMEOUT_COMMAND:-/usr/bin/timeout}
  local ionice_command=${ROOTFS_IONICE_COMMAND:-/usr/bin/ionice}
  local nice_command=${ROOTFS_NICE_COMMAND:-/usr/bin/nice}
  local du_command=${ROOTFS_DU_COMMAND:-/usr/bin/du}

  ROOTFS_MEASURED_MIB=()
  ROOTFS_MEASURED_PATHS=()
  ROOTFS_FAILED_PATHS=()

  for entry in "${ROOTFS_PATH_BUDGETS_MIB[@]}"; do
    path=${entry%:*}
    budget_mib=${entry##*:}
    if [[ "$path" != /* || "$path" == "$entry" ||
          ! "$budget_mib" =~ ^[1-9][0-9]*$ ]]; then
      rootfs_add_problem "invalid rootfs path budget: $entry"
      ROOTFS_FAILED_PATHS+=("$path")
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
  local output result line value=
  local df_command=${ROOTFS_DF_COMMAND:-/bin/df}

  output=$("$df_command" -k --output=used / 2>&1)
  result=$?
  if (( result != 0 )); then
    rootfs_add_problem "could not measure rootfs usage (df exit $result)"
    return 1
  fi
  while IFS= read -r line; do
    line=${line//[[:space:]]/}
    [[ "$line" =~ ^[0-9]+$ ]] && value=$line
  done <<< "$output"
  if [[ -z "$value" ]]; then
    rootfs_add_problem "could not parse rootfs usage"
    return 1
  fi
  ROOTFS_USED_MIB=$((value / 1024))
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
  problems+=("${context%;}")
}

check_rootfs_budget() {
  local now=${1:?epoch required} used_gib
  ROOTFS_PROBLEM=0
  ROOTFS_USED_MIB=0

  rootfs_measure_budget_paths
  if rootfs_read_used_mib; then
    used_gib=$((ROOTFS_USED_MIB / 1024))
    status+="rootfs: ${used_gib}GiB used. "
    if (( used_gib > ROOT_USED_MAX_GB )); then
      rootfs_add_problem "rootfs uses ${used_gib}GiB (limit ${ROOT_USED_MAX_GB}GiB for configured ${CLONE_CARD_NOMINAL_GB}GB clone cards)"
    fi
    rootfs_check_growth "$now"
  else
    status+="rootfs: measurement failed. "
  fi
  rootfs_append_context
}
