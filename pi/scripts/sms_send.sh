#! /bin/bash

source /home/pi/.twilio/twilio_creds.sh
url="https://api.twilio.com/2010-04-01/Accounts/$TWILIO_ACCOUNT_SID/Messages"
# curl -X POST -d "Body=$1" -d "From=$TWILIO_NUMBER" -d "To=$TO_NUMBER" $url -u "$TWILIO_ACCOUNT_SID:$TWILIO_AUTH_TOKEN"

curl -X POST $url \
  --data-urlencode "To=$TO_NUMBER" \
  --data-urlencode "From=$TWILIO_NUMBER" \
  --data-urlencode "Body=$(echo -e $1)" \
  -u "$TWILIO_ACCOUNT_SID:$TWILIO_AUTH_TOKEN"