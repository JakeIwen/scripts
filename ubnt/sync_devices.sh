#! /bin/bash 
# set up your ssh key first


# exec_on 'scp -r ubnt@192.168.8.20:/etc/persistent/profiles ~/dev/scripts/ubnt/persistent/' $subnets

# exec_on 'scp -r ~/dev/scripts/ubnt/persistent/ ubnt@192.168.$cmd.20:/etc/' $subnets

# exec_on ssh ubnt@192.168.8.20 'cfgmtd -w -p /etc/' $subnets
build_ips() {
  for net in "$@"; do 
    ip="192.168.$net.20" 
    echo $ip
    if ping -c 1 $ip &> /dev/null; then echo "$ip" ; fi
  done 
} 

bd_cmd() {
  ip=$1
  idx=$2
  declare -a cmds=(
    "echo HEYYYY IP $ip"
    "scp -r ubnt@$ip:/etc/persistent/profiles ~/dev/scripts/ubnt/persistent/"
    "scp -r ~/dev/scripts/ubnt/persistent/ ubnt@$ip:/etc/"
    "ssh ubnt@$ip 'cfgmtd -w -p /etc/'"
  )
  echo "${cmds[$idx]}"
}

exec_on() {
  # args=`echo "$@" | cut -d ' ' --complement -f 1`
  ips=`build_ips "$@"`
  
  for i in 0 1 2; do
    for ip in $ips; do 
      cmd=`bd_cmd $ip $i`
      echo "$cmd"
      $cmd
    done 
  done

  
  # $2 && if ping -c 1 $ip1 &> /dev/null; then `$cmd` ; fi
  # $3 && if ping -c 1 $ip2 &> /dev/null; then `$cmd` ; fi
  # $4 && if ping -c 1 $ip3 &> /dev/null; then `$cmd` ; fi
  # $5 && if ping -c 1 $ip4 &> /dev/null; then `$cmd` ; fi
}

exec_on 6 7 8 10

