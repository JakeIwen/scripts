

local_addrs=( `sudo arp-scan --interface=en0 --localnet | ggrep -P '^192\.' | cut -f1 | sort -u` )

for ip in "${local_addrs[@]}"; do ( curl $ip > /dev/null && echo "{{{$ip}}}" &  ); done
