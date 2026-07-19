#!/bin/ash

# Check arguments
if [ -z "$1" ] || [ -z "$2" ]; then
    echo "Usage: $0 /path/to/directory minutes"
    exit 1
fi

DIR="$1"
MINUTES="$2"

# Validate directory
if [ ! -d "$DIR" ]; then
    echo "Error: Directory does not exist"
    exit 1
fi

# Validate minutes is a number
case "$MINUTES" in
    ''|*[!0-9]*)
        echo "Error: minutes must be a positive integer"
        exit 1
        ;;
esac

# Delete files older than or equal to specified minutes.
# BusyBox find on some systems does not support -mmin, so compute age manually.
NOW="$(date +%s)"

find "$DIR" -type f | while IFS= read -r FILE; do
    # Prefer BusyBox-compatible date -r first.
    MTIME="$(date -r "$FILE" +%s 2>/dev/null)"
    if [ -z "$MTIME" ]; then
        MTIME="$(stat -c %Y "$FILE" 2>/dev/null)"
    fi
    if [ -z "$MTIME" ]; then
        MTIME="$(stat -f %m "$FILE" 2>/dev/null)"
    fi

    # Skip files we cannot read mtime for on this platform.
    if [ -z "$MTIME" ]; then
        continue
    fi

    AGE_MINUTES=$(( (NOW - MTIME) / 60 ))
    if [ "$AGE_MINUTES" -ge "$MINUTES" ]; then
        rm -f "$FILE"
    fi
done

exit 0