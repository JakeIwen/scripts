join_by() { local IFS="$1"; shift; echo "$*"; }

pk() { # kill process by name match - append flag '-9' for SIGTERM
  search_terms=$*
  k9="${@: -1}" # remove k9 arg
  if [[ $k9 == '-9' ]]; then search_terms=${@:1:$#-1}; else k9=""; fi
  echo "k9: k9"
  joined_terms=`join_by '|' $(echo $search_terms)`
  pids=`pgrep -fi "$joined_terms" | sed "s|$$||g"`
  if [[ ! $pids ]]; then echo "no match" && return 0; fi
  # echo $pids | while read -r pid; do sudo kill $k9 $pid; done
  echo $pids | xargs kill $k9
  sleep 2
  alive=`pgrep -fi "$joined_terms" | sed "s|$$||g"`
  if [[ $alive ]]; then 
    echo "still alive: $alive - trying kill9"
    echo $pids | xargs kill -9
    if [[ $alive ]]; then echo "still alive: $alive"; else echo "terminated $pids"; fi
  fi
}

pk searchparty 
pk universalacc
