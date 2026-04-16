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

# Delete files older than specified minutes
find "$DIR" -type f -mmin +"$MINUTES" -exec rm -f {} \;

exit 0