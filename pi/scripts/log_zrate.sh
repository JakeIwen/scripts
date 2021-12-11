#! /bin/bash

z_activpath=/home/pi/log/zuseractive.txt
z_logpath=/home/pi/log/zrate.txt

get_offers() {  curl -sSL "https://bisq.markets/api/offers?market=BTC_USD"; }
pp_json() { echo "$1" | python -m json.tool; }
btclow() { parse_prices | sort -rn | tail -n 1; }
btcusd() { curl -sSL https://api.coinbase.com/v2/prices/spot\?currency\=USD | grep -Po '\d+\.\d+'; }
usd_btc_rate() {  echo "$(bc <<< "scale=2; (100 * ($1 - `btcusd`) / `btcusd` - 0.35)")"; }
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

active=$(echo "$offers" | grep JHYY1)
touch $z_activpath
if [ -n "$active" ]; then
  echo "BISQ USER JHYY1 IS ACTIVE - $(date)"
  echo "BISQ USER JHYY1 IS ACTIVE - $(date)" >> $z_activpath
else
  echo "inactive - $(date)"
  echo "inactive - $(date)" >> $z_activpath
fi

cur_rate=`usd_btc_rate "$(btclow)"`
if [[ ! $cur_rate ]] || [[ "$(echo $cur_rate | grep -P '^\d\d\d')" ]]; then # || [[ "$(echo $cur_rate | grep -P '^\-')" ]]
  echo "no offers found" 
else
  touch $z_logpath
  echo "$cur_rate,$(date +%s),$(date)" >> $z_logpath
  echo "cur_rate: $cur_rate"
fi


# awk 'NR % 60 == 0'  $z_logpath
# sed  -i '/^,.*/d' $z_logpath 
# sed  -i '/^-.*/d' $z_logpath 
