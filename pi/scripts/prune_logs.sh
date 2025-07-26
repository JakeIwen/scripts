#!/bin/bash

# Usage: ./prune_logs.sh /path/to/logs 1000

set -euo pipefail

# Validate arguments
if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <log_directory> <num_lines>"
  exit 1
fi

log_dir="$1"
num_lines="$2"

# Check if directory exists
if [[ ! -d "$log_dir" ]]; then
  echo "Error: '$log_dir' is not a valid directory."
  exit 1
fi

# Ensure num_lines is a positive integer
if ! [[ "$num_lines" =~ ^[0-9]+$ ]] || [[ "$num_lines" -eq 0 ]]; then
  echo "Error: Number of lines must be a positive integer."
  exit 1
fi

# Find and prune .log files
find "$log_dir" -type f -name "*.log" | while read -r logfile; do
  echo "Pruning $logfile to last $num_lines lines..."
  
  tmpfile=$(mktemp)

  # Extract last N lines
  tail -n "$num_lines" "$logfile" > "$tmpfile"

  # Preserve original file's owner, group, and permissions
  owner=$(stat -c '%u' "$logfile")
  group=$(stat -c '%g' "$logfile")
  perms=$(stat -c '%a' "$logfile")

  # Replace original with trimmed version
  mv "$tmpfile" "$logfile"
  chown "$owner:$group" "$logfile"
  chmod "$perms" "$logfile"
done

echo "Pruning complete."