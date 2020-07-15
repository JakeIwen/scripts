# set up your ssh key first
scp -r ~/dev/scripts/ubnt/persistent/ ubnt@192.168.8.20:/etc/
ssh ubnt@192.168.8.20 save