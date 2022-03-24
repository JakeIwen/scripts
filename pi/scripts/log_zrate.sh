#! /bin/bash

z_logpath=/home/pi/log/zrate.txt
min_pct_fee=1


last_zratee() { cat $z_logpath | tail -1 | cut -f1 -d , ; }
get_offers() { curl -sSL "https://bisq.markets/api/offers?market=BTC_USD"; }
pp_json() { echo "$1" | python -m json.tool; }
btclow() { parse_prices | sort -rn | tail -n 1; }
btcusd() { curl -sSL https://api.coinbase.com/v2/prices/spot\?currency\=USD | grep -Po '\d+\.\d+'; }
usd_btc_rate() { echo "$(bc <<< "scale=2; (100 * ($1 - `btcusd`) / `btcusd` - 0.35)")"; }
parse_prices() {
  pp_json "$offers" | 
  grep '"direction": "SELL"' -A 7 | 
  grep -P '"min_amount": "0\.0[0-3]\d*' -A 6 | # less than 1/25 BTC (~$2k) 
  grep CLEAR_X_ -A 1 |  # zelle 
  grep -Po '\d+\.\d+'
}
# is_less_than_usd() {
#   if (( $(echo "$1 < $result2" | bc -l) )); then
# }

offers="$(get_offers)"
if [ "$(parse_prices | wc -l)" -lt 5 ]; then 
  echo "$(parse_prices | wc -l) offers, getting prices again"
  sleep 5
  offers="$(get_offers)"
fi
echo "$(parse_prices | wc -l) offers"


cur_rate=`usd_btc_rate "$(btclow)"`
if [[ ! "$cur_rate" ]] || [[ "$(echo $cur_rate | grep -P '\d\d\d')" ]]; then # triple digit rate means bad API response
  echo "no offers found (rate (mis)calculated at: $cur_rate)" 
  return 0 2>/dev/null 
  exit 0
  echo "this code still should not print"
fi
touch $z_logpath
prev_rate="$(cat $z_logpath | tail -1 | cut -f1 -d , )"
echo "$cur_rate,$(date +%s),$(date)" >> $z_logpath
echo "cur_rate: $cur_rate"

compare() { (( $(echo "$1" | bc -l) )) && echo true; }

if compare "$prev_rate >= $min_pct_fee"; then
  compare "$cur_rate < $min_pct_fee" && /home/pi/scripts/sms_send.sh "low zrate: $cur_rate"
else
  compare "$cur_rate >= $min_pct_fee" && /home/pi/scripts/sms_send.sh "zrate returned above threshold: $cur_rate"
fi

# awk 'NR % 60 == 0'  $z_logpath
# sed  -i '/^,.*/d' $z_logpath 
# sed  -i '/^-.*/d' $z_logpath 
