# set up your ssh key first
scp -r ubnt@192.168.8.20:/etc/persistent ~/dev/scripts/ubnt/
scp -r ~/dev/scripts/ubnt/persistent/ ubnt@192.168.8.20:/etc/
ssh ubnt@192.168.8.20 'cfgmtd -w -p /etc/'
ssh ubnt@192.168.8.20 'pkill -f cron'
ssh ubnt@192.168.8.20 'sh /etc/persistent/rc.postsysinit'
ssh ubnt@192.168.8.20 'crond'