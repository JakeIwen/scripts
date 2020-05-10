
# NETWORKS=$(ECHO iwlist $1 scan 2>/dev/null | awk -f /etc/persistent/config/parse-iwlist.awk | sort -t$'\t' -nrk3)
# scanres=$(echo iwlist ath0 scan)
# iwlist ath0 scan | awk '/ESSID/{i++}i==2'\

ssid_scan_path=/etc/persistent/config/essids.XXXXX
ssid_list_path=/etc/persistent/config/ssids.sh

append_networks ()
{
  iwlist ath0 scan 2>/dev/null | awk -f /etc/persistent/config/parse-iwlist.awk >> $ssid_scan_path
  sleep 3
}

sort_networks ()
{
  cat $ssid_scan_path | awk '{print $NF,$0}' | cut -f3- -d':' | sort -nr | cut -f2- -d' '
}

set_ap ()
{
  cp ~/config/profiles/"$SSID".cfg /tmp/system.cfg
  /usr/etc/rc.d/rc.softrestart save
}

mark_finish ()
{
  echo $(date)
  echo "end"
  echo " "
}

find_ap ()
{
  echo "begin"
  if [[ "$WAITING_NEW_AP" = "true" ]]; then
    echo "waiting on recently set AP"
    mark_finish
  fi
  > $ssid_scan_path
  append_networks
  append_networks
  append_networks
  
  current_ssid=$(iwgetid -r)
  scan_results=$(sort_networks)
  saved_networks=$(cat $ssid_list_path)
  echo "current_ssid: $current_ssid"
  IFS=$'\n'
  for i in $saved_networks 
  do 
    saved_ssid=`echo "$i" | cut -d "," -f 1`
    echo "saved_ssid: $saved_ssid"
    if [[ "$saved_ssid" = "$current_ssid" ]]; then
      echo "skipping current SSID"
      continue
    fi
    for j in $scan_results
      do 
        scan_ssid=`echo "$j" | cut -d ":" -f 1`
        echo "scan_ssid: $scan_ssid"
        if [[ "$saved_ssid" = "$scan_ssid" ]]; then
          echo "MATCH: $j"
          PSK=`echo "$i" | cut -d "," -f 2`
          SSID="$scan_ssid"
          SECTYPE=`echo "$j" | cut -d ":" -f 2`
          set_ap
          echo "setting AP"
          WAITING_NEW_AP="true"
          sleep 120
          WAITING_NEW_AP="false"
          echo "AP set"
          mark_finish
          return 0
        fi
      done
  done
}

# # run every minute

if ping -q -c 1 -W 1 8.8.8.8 >/dev/null; then
  echo "Ipv4 is up"
else
  find_ap
fi