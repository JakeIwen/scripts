
# NETWORKS=$(ECHO iwlist $1 scan 2>/dev/null | awk -f /etc/persistent/config/parse-iwlist.awk | sort -t$'\t' -nrk3)
# scanres=$(echo iwlist ath0 scan)
# iwlist ath0 scan | awk '/ESSID/{i++}i==2'\

ssid_scan_path="/etc/persistent/scripts/essids.XXXXX"
cur_profile_path="/etc/persistent/tmp/cur_profile.cfg"
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
  pkill -f crond
  sleep 120
  echo "AP set. Restarting cron at $(date)"
  crond
}

find_ap ()
{
  if [ "$(cat $cur_profile_path)" = "$(cat /tmp/system.cfg)" ]; then
    echo "profile unchanged, continuing"
  else
    echo "waiting on manually set AP. Killing cron for 140s"
    pkill -f crond
    sleep 140
    crond
    save_tmp_profile
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
        echo " "
        return 0
      fi
    done
  done
}

save_current_profile ()
{
  ssid=$(iwgetid -r)
  profile_path="/etc/persistent/profiles/$ssid"
  if [ "$(cat $profile_path)" = "$(cat /tmp/system.cfg)" ]; then
    echo "profile unchanged, done."
  else
    echo "saving/updating profile: $ssid"
    cp /tmp/system.cfg "$profile_path"
    chmod 755 "$profile_path"
    cfgmtd -w -p /etc/
  fi
}

save_tmp_profile ()
{
  cp /tmp/system.cfg "$cur_profile_path"
  chmod 755 "$cur_profile_path"
}

# # run every minute

ccq=`mca-status | grep ccq | cut -d= -f2`
if [[ $ccq -gt 300 ]]; then
  echo "Ipv4 is up"
  save_current_profile
else
  find_ap
fi





# ensure_saved ()
# {
#   cur=$(iwgetid -r)
#   list=$(ls /etc/persistent/profiles)
#   profile_is_saved=False
#   for item in $list
#   do
#     if [[ "$cur" = "$item" ]]; then
#       profile_is_saved=True
#     fi
#   done
# 
#   if [[ $profile_is_saved = False ]]; then
#     echo "SAVING NEW PROFILE: $cur"
#     save_current_profile
#   fi
# }