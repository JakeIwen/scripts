
# NETWORKS=$(ECHO iwlist $1 scan 2>/dev/null | awk -f /etc/persistent/config/parse-iwlist.awk | sort -t$'\t' -nrk3)
# scanres=$(echo iwlist ath0 scan)
# iwlist ath0 scan | awk '/ESSID/{i++}i==2'\

ssid_scan_path="/etc/persistent/scripts/essids.XXXXX"

append_networks ()
{
  iwlist ath0 scan 2>/dev/null | awk -f /etc/persistent/scripts/parse-iwlist.awk >> $ssid_scan_path
  sleep 3
}

sort_networks ()
{
  cat $ssid_scan_path | awk '{print $NF,$0}' | sort -nr | cut -d ' ' -f 2 | uniq
}

set_ap ()
{
  cp "/etc/persistent/profiles/$1" /tmp/system.cfg
  /usr/etc/rc.d/rc.softrestart save
}

find_ap ()
{
  if [[ "$WAITING_NEW_AP" = "true" ]]; then
    echo "waiting on recently set AP"
    return 0
  fi
  rm $ssid_scan_path && touch $ssid_scan_path
  append_networks
  append_networks
  append_networks
  
  current_ssid=$(iwgetid -r)
  scan_results=$(sort_networks)
  saved_networks=$(ls /etc/persistent/profiles)
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
      scan_ssid=`echo "$j" | cut -f 1`
      echo "scan_ssid: $scan_ssid"
      if [[ "$saved_ssid" = "$scan_ssid" ]]; then
        echo "MATCH: $j - setting AP"
        set_ap $scan_ssid
        pkill -f crond
        sleep 120
        echo "AP set. Restarting cron at $(date)"
        echo " "
        crond
        return 0
      fi
    done
  done
}

save_current_profile ()
{
  ssid=$(iwgetid -r)
  cp /tmp/system.cfg "/etc/persistent/profiles/$ssid"
  chmod 755 "/etc/persistent/profiles/$ssid"
  cfgmtd -w -p /etc/
}

ensure_saved ()
{
  cur=$(iwgetid -r)
  list=$(ls /etc/persistent/profiles)
  profile_is_saved=False
  for item in $list
  do
    if [[ "$cur" = "$item" ]]; then
      profile_is_saved=True
    fi
  done
  
  if [[ $profile_is_saved = False ]]; then
    echo "SAVING NEW PROFILE: $cur"
    save_current_profile
  fi
}

# # run every minute

if ping -q -c 1 -W 1 8.8.8.8 >/dev/null; then
  echo "Ipv4 is up"
  ensure_saved
else
  find_ap
fi