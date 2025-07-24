# set up your ssh key first
scp -r -O ubnt@192.168.8.20:/etc/persistent/profiles ~/dev/scripts/ubnt/persistent
scp -r -O ~/dev/scripts/ubnt/persistent/ ubnt@192.168.8.20:/etc/
ssh ubnt@192.168.8.20 'cfgmtd -w -p /etc/'
ssh ubnt@192.168.8.20 'sh /etc/persistent/rc.postsysinit'
opkg update && opkg list-installed | cut -f 1 -d ' ' | sort -u > /tmp/currentpkg && cat /etc/config/my_installed_packages | cut -f 1 -d ' ' | sort -u > /tmp/oldpkg && grep -v -F -x -f /tmp/currentpkg /tmp/oldpkg > /tmp/inst && opkg install $(cat /tmp/inst | sort -u) && rm /tmp/currentpkg /tmp/oldpkg /tmp/inst