#! /bin/bash
join_by() { local IFS="$1"; shift; echo "$*"; }

pk() { # kill process by name match - append flag '-9' for SIGTERM
  search_terms=$*
  k9="${@: -1}" # remove k9 arg
  if [[ $k9 == '-9' ]]; then search_terms=${@:1:-1}; else k9=""; fi
  joined_terms=`join_by '|' $(echo $search_terms)`
  pids=`pgrep -fi "$joined_terms" | sed "s|$$||g"`
  if [[ ! $pids ]]; then echo "no match" && return 0; fi
  echo $pids | while read -r pid; do sudo kill $k9 $pid; done
  sleep 1
  alive=`pgrep -fi "$joined_terms"`
  if [[ $alive ]]; then 
    echo "still alive: $alive"
    echo "re run with '-9' to SIGKILL"
  else
    echo "terminated $pids"
  fi
}

pk qbit
. /home/pi/scripts/umount_disks.sh 
. /home/pi/scripts/mount_disks.sh 
sudo service smbd start
