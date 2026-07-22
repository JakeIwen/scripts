#! /bin/bash
crontab="$1" # "* * * * *"

curl -s https://www.uptimia.com/cron-expression-generator-action \
    --data-urlencode "expression=$crontab" | jq -r '.text'