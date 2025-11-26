
#!/bin/bash

tuya_toggle="$HOME/scripts/tuya_toggle.sh"

# Get the current hour in 24-hour format (00–23)
HOUR=$(date +%H)

# Check if hour is between 07 (inclusive) and 17 (exclusive)
if [ "$HOUR" -ge 7 ] && [ "$HOUR" -lt 17 ]; then
  $tuya_toggle ext_flood off
fi
