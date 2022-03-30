#! /bin/bash

z_logpath=/home/pi/log/zrate.txt
min_pct_fee=0.2

if [ -z "`which grep`" ]; then grep() { grep $*; }; fi

compare() { (( $(echo "$1" | bc -l) )) && echo true; }
get_offers() { curl -sSL "https://bisq.markets/api/offers?market=BTC_USD"; }
pp_json() { echo "$1" | python -m json.tool; }
btclow() { parse_prices | sort -rn | tail -n 1; }
btcusd() { curl -sSL https://api.coinbase.com/v2/prices/spot\?currency\=USD | grep -Po '\d+\.\d+'; }
usd_btc_rate() { echo "$(bc <<< "scale=2; (100 * ($1 - `btcusd`) / `btcusd` - 0.35)")"; }
num_offers() { parse_prices | wc -l; }
sms_send() { /home/pi/scripts/sms_send.sh "$1" >/dev/null; }
parse_prices() {
  pp_json "$offers" | 
  grep '"direction": "SELL"' -A 7 | 
  # grep -P '"min_amount": "0\.0[0-3]\d*' -A 6 | # less than 1/25 BTC (~$2k) 
  grep CLEAR_X_ -A 1 |  # zelle 
  grep -Po '\d+\.\d+'
}

get_offer_details() {
  pp_json "$offers" | 
  grep '"direction": "SELL"' -A 8  -B 2 | 
  grep CLEAR_X_ -A 2 -B 6 |
  grep "$(btclow)" -A 1 -B 7
}

get_filtered_offers() {
  bad_entry="$(echo "$1" | grep -Pzo '(?s)\{"offer_id":"NfDEN7.*?\},')"
  if [ -n "$bad_entry" ]; then 
    echo "$1" | sed "s|$bad_entry||g"
  else
    echo "$1"
  fi
}

raw_offers="$(get_offers)"
offers="$(get_filtered_offers $raw_offers)"

if [ "$(num_offers)" -lt 5 ]; then 
  echo "$(num_offers) offers, getting prices again"
  sleep 5
  raw_offers="$(get_offers)"
  offers="$(get_filtered_offers $raw_offers)"
fi

echo "raw wc: $(pp_json $raw_offers | wc -l)"
echo "filtered wc: $(pp_json $offers | wc -l)"

cur_rate=`usd_btc_rate "$(btclow)"`
if [[ ! "$cur_rate" ]] || [[ "$(echo $cur_rate | grep -P '\d\d\d')" ]]; then # triple digit rate means bad API response
  echo "no offers found (rate (mis)calculated at: $cur_rate)" 
  return 0 2>/dev/null 
  exit 0
fi

touch $z_logpath
last_rate="$(cat $z_logpath | tail -1 | cut -f1 -d , )"
echo "$cur_rate,$(date +%s),$(date)" >> $z_logpath
echo "cur_rate: $cur_rate"
echo "last_rate: $last_rate"

newly_lz_msg() {
  details="$(get_offer_details)"
  then_ms=$(echo $details | grep -Po '\d{13}')
  now_ms=$(date +%s%N | cut -b1-13) 
  minutes_age=$(echo "scale=2;($now_ms-$then_ms)/1000/60" | bc)
  
  msg="sustained low zrate: $(btclow) (${cur_rate}%)\n"
  msg+="age: $minutes_age minutes \n"
  msg+="$(num_offers) offers \n\n"
  # echo "$msg$details"
}
if compare "$last_rate > $min_pct_fee"; then 
  echo "rate above threshold" 
  return 0 2>/dev/null || exit 0
fi

if compare "$cur_rate < $min_pct_fee"; then
  msg=`newly_lz_msg`
  compare "$last_rate < $min_pct_fee" && echo "Sending SMS: $msg" && sms_send "$msg"
elif compare "$cur_rate >= $min_pct_fee"; then
   sms_send "zrate returned above threshold: $cur_rate"
fi

# awk 'NR % 60 == 0'  $z_logpath
# sed  -i '/^,.*/d' $z_logpath 
# sed  -i '/^-.*/d' $z_logpath 
