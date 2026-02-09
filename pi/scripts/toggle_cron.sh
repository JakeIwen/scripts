#!/bin/bash

# usage toggle_cron.sh streetlight

set -euo pipefail

JOB_NAME="$1"

if [[ -z "${JOB_NAME:-}" ]]; then
  echo "Usage: $0 <job_name>"
  exit 1
fi

TMP_CRON="$(mktemp)"

# Dump current crontab
crontab -l > "$TMP_CRON"

# Check if job exists at all
if ! grep -q "${JOB_NAME}\.sh" "$TMP_CRON"; then
  echo "Error: No cron job found containing '${JOB_NAME}.sh'"
  rm -f "$TMP_CRON"
  exit 1
fi

# Toggle comment state
awk -v job="${JOB_NAME}.sh" '
{
  if ($0 ~ job) {
    if ($0 ~ /^[[:space:]]*#/) {
      sub(/^[[:space:]]*# ?/, "", $0)
      print $0
    } else {
      print "# " $0
    }
  } else {
    print $0
  }
}
' "$TMP_CRON" > "${TMP_CRON}.new"

# Install updated crontab
crontab "${TMP_CRON}.new"

rm -f "$TMP_CRON" "${TMP_CRON}.new"

echo "Toggled cron job: ${JOB_NAME}"
