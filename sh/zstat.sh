
pp_json() { echo "$1" | python -m json.tool; }
btclow() { parse_prices | sort -rn | tail -n 1; }
btcusd() { curl -sSL https://api.coinbase.com/v2/prices/spot\?currency\=USD | ggrep -Po '\d+\.\d+'; }
usd_btc_rate() {  echo "$(bc <<< "scale=2; (100 * ($1 - `btcusd`) / `btcusd` - 0.35)")"; }
parse_prices() { 
  pp_json "$offers" | 
  ggrep '"direction": "SELL"' -A 7 | 
  ggrep -P '"min_amount": "0\.0[0-3]\d*' -A 6 | # less than 1/25 BTC (~$2k) 
  ggrep CLEAR_X_ -A 1 |  # zelle 
  ggrep -Po '\d+\.\d+'
}
# is_less_than_usd() {
#   if (( $(echo "$1 < $result2" | bc -l) )); then
# }

offers="$(curl -sSL 'https://bisq.markets/api/offers?market=BTC_USD')"
if [ "$(parse_prices | wc -l)" -lt 5 ]; then 
  echo "$(parse_prices | wc -l) offers, getting prices again"
  sleep 5
  offers="$(curl -sSL 'https://bisq.markets/api/offers?market=BTC_USD')"
fi
echo "$(parse_prices | wc -l) offers"

active=$(echo "$offers" | ggrep JHYY1)
if [ -n "$active" ]; then
  echo "BISQ USER JHYY1 IS ACTIVE - $(date)"
fi

cur_rate=`usd_btc_rate "$(btclow)"`
if [[ ! "$cur_rate" ]] || [[ "$(echo $cur_rate | ggrep -P '^\d\d\d')" ]]; then # || [[ "$(echo $cur_rate | ggrep -P '^\-')" ]]
  echo "no offers found" 
else
  echo "$cur_rate"
fi

