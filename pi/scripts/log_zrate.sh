#! /bin/bash

z_logpath=/home/pi/log/zrate.txt

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
if [[ ! "$cur_rate" ]] || [[ "$(echo $cur_rate | grep -P '^\d\d\d')" ]]; then # || [[ "$(echo $cur_rate | grep -P '^\-')" ]]
  echo "no offers found" 
<<<<<<< HEAD
  return 0
  echo "this code should not print"
=======
else
  touch $z_logpath
  if echo $cur_rate | grep -v '00\.35'; then
    echo "$cur_rate,$(date +%s),$(date)" >> $z_logpath
  fi
  echo "cur_rate: $cur_rate"
>>>>>>> 81e311d22574d3c286ecc8967feb371606a10674
fi
touch $z_logpath
echo "$cur_rate,$(date +%s),$(date)" >> $z_logpath
echo "cur_rate: $cur_rate"

zrate_less_than() {
  [[ "$cur_rate" ]] && (( $(echo "$cur_rate < $1" | bc -l) )) && echo "LOW ZRATE: $cur_rate"
}

if zrate_less_than 0; then
  . /home/pi/scripts/sms_send.sh "low zrate: $cur_rate"
fi

# awk 'NR % 60 == 0'  $z_logpath
# sed  -i '/^,.*/d' $z_logpath 
# sed  -i '/^-.*/d' $z_logpath 
