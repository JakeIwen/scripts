ccq=`mca-status | grep ccq | cut -d= -f2`
if [[ $ccq -gt 300 ]]; then
  echo "link is up, not rebooting"
else
  reboot
fi