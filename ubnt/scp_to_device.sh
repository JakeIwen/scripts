# set up your ssh key first
scp -r ubnt@192.168.8.20:/etc/persistent/profiles ~/dev/scripts/ubnt/persistent
scp -r ~/dev/scripts/ubnt/persistent/ ubnt@192.168.8.20:/etc/
ssh ubnt@192.168.8.20 'cfgmtd -w -p /etc/'
ssh ubnt@192.168.8.20 'sh /etc/persistent/rc.postsysinit'
