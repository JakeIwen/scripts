#! /bin/bash

zrates_am_pm=$HOME/log/zrate.txt
zrates_24=$HOME/log/zrate24.txt

days=(Sun Mon Tue Wed Thu Fri Sat)
hours=(00 01 02 03 04 05 06 07 08 09 10 11 12 13 14 15 16 17 18 19 20 21 22 23)

[[ "$1" == 'd' ]] && days=("") 
[[ "$1" == 'h' ]] && hours=("")

zrate_by_hour() { # usage: zrate_by_hour Fri 09 AM
  d=${1:-\\w\\w\\w}
  h=${2:-\\d\\d}
  filtered=`grep -Pa "$d .* $h:" $zrates_24 | grep -Poa '\-?\d?\d?\.\d+'`
  num_lines=`echo "$filtered"  | wc -l`
  avgs=`echo "$filtered" | awk '{if(min==""){min=max=$1}; if($1>max) {max=$1}; if($1<min) {min=$1}; total+=$1; count+=1} END {print total/count," | max "max," | min " min}'`
  echo "$avgs | n=$num_lines"
}

convert_24_hour() {
  cat $1 | perl -pe 's{\b(\d{1,2})(:\d\d:\d\d) ([AP])M\b}{ 
  $1 + 12 * (($3 eq "P") - ($1 == 12)) . $2}ge' | perl -pe 's| (\d:\d\d:\d\d)| 0\1|g'
}

convert_24_hour $zrates_am_pm > $zrates_24

data=''

for day in "${days[@]}"; do
  for hour in "${hours[@]}"; do
    data+="$(zrate_by_hour "$day" "$hour") | $day $hour:XX\n"
  done
done

echo -e "$data" | column -t | perl -pe 's|(\S)  |\1 |g' | sort

alias print_zrate="cat ~/log/zrate.txt"
alias zrate_hourly="awk 'NR % 60 == 0' ~/log/zrate.txt"
zrate_by_hour() { # usage: zrate_by_hour Fri 09 AM
  grep -Pa "$1.* $2:.*$3" ~/log/zrate.txt | grep -Poa '\-?\d?\d?\.\d+' | awk '{if(min==""){min=max=$1}; if($1>max) {max=$1}; if($1<min) {min=$1}; total+=$1; count+=1} END {print total/count," | max "max," | min " min}';
}
zrate_stat(){ grep -Poa '\-?\d?\d?\.\d+' ~/log/zrate.txt | awk '{if(min==""){min=max=$1}; if($1>max) {max=$1}; if($1<min) {min=$1}; total+=$1; count+=1} END {print "avg " total/count," | max "max," | min " min}'; }
zrate_less_than() {
  lzr=`tail -1 ~/log/zrate.txt | grep -Po '^\d?\d?\.\d+'`
  [[ "$lzr" ]] && (( $(echo "$lzr < $1" | bc -l) )) && echo "LOW ZRATE: $lzr"
}
alias last_zrate="tail -1 ~/log/zrate.txt"
alias bisqactive="cat ~/log/zuseractive.txt | grep JHYY1"