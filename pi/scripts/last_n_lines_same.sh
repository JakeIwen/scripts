#!/bin/bash

file="$1"
n="$2"

# Check for missing arguments
if [ -z "$file" ] || [ -z "$n" ]; then
  echo "Usage: $0 <file> <n>"
  exit 1
fi

# Check if file exists
if [ ! -f "$file" ]; then
  echo "File not found: $file"
  exit 1
fi

# Check total line count
total_lines=$(wc -l < "$file")

if [ "$total_lines" -lt "$n" ]; then
  echo "File has fewer than $n lines"
  exit 2
fi

# Check if the last n lines are the same
if [ "$(tail -n "$n" "$file" | uniq | wc -l)" -eq 1 ]; then
  echo "Last $n lines are the same"
  exit 0
else
  echo "Last $n lines differ"
  exit 3
fi