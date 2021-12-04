#! /bin/bash


z_activpath=/home/pi/log/zuseractive.txt
z_logpath=/home/pi/log/zrate.txt
offers=`curl -sSL "https://bisq.markets/api/offers?market=BTC_USD"`
pp_json() { echo $1 | python -m json.tool; }
btclow() { pp_json "`echo $offers`" | grep '"direction": "SELL"' -A 7 | grep CLEAR_X_ -A 1 | grep -Po '\d+\.\d+' | sort -rn | tail -n 1; }
btcusd() { curl -sSL https://api.coinbase.com/v2/prices/spot\?currency\=USD | grep -Po '\d+\.\d+'; }
zrate() {  echo "$(bc <<< "scale=2; (100 * (`btclow` - `btcusd`) / `btcusd` - 0.35)")"; }

active=$(echo "$offers" | grep JHYY1)
touch $z_activpath
if [ -n "$active" ]; then
  echo "BISQ USER JHYY1 IS ACTIVE - $(date)"
  echo "BISQ USER JHYY1 IS ACTIVE - $(date)" >> $z_activpath
else
  echo "inactive - $(date)"
  echo "inactive - $(date)" >> $z_activpath
fi


# if date | grep -P '[0,5]:\d\d '; then echo 'continuing'; else exit 0; fi


cur_rate=`zrate`
if [[ ! $cur_rate ]] || [[ "$(echo $cur_rate | grep -P '^\D')" ]] || [[ "$(echo $cur_rate | grep -P '^\d\d\d')" ]]; then 
  echo "no offers found" 
else
  touch $z_logpath
  echo "$cur_rate,$(date +%s),$(date)" >> $z_logpath
fi


# awk 'NR % 60 == 0'  $z_logpath
# sed  -i '/^,.*/d' $z_logpath 
# sed  -i '/^-.*/d' $z_logpath 
