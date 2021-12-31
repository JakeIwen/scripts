#! /bin/bash

# mntdsk sd_card 0383-ABDF
mntdsk() {
  fname=$1
  uuid=$2
  pth="/mnt/$fname"
  fstype=`blkid | grep $uuid | grep -Po '(?<=TYPE=")[^"]*'`

  if [[ "$fstype" == "hfsplus" ]]; then
    opts="-o force,rw"
  else
    opts=""
  fi
  
  sudo mkdir -p $pth && sudo chown pi $pth  && sudo chmod 777 $pth 
  sudo mount -U "$uuid" -t $fstype $opts $pth && echo "mounted $fname at $pth"
}

mntdsk mbbackup "8790cc1e-e5ca-372e-b846-8cedd282e9bf"
mntdsk movingparts "b3f3432a-57a0-4fb7-bb54-db528d15bca7"
mntdsk bigboi "313cb347-2278-42d0-8a2c-f1dde1e82725"
mntdsk seegayte "40c2277a-91f3-440f-a9cb-8417e9d64e03"

# mntdsk msd_nand2_root "45a27acc-38c2-4794-95ce-61d8bf32d0fd"
# mntdsk msd_nand2_boot "0383-ABDF"
# mntdsk msd_nand2_settings "d815cc0b-cb38-4c7d-a029-631fb74fe785"
# 
# mntdsk msd_nand1_root "2370748c-1a9f-4dfb-b5d7-97a04e69030a"
# mntdsk msd_nand1_boot "0155-9582"
# mntdsk msd_nand1_settings "84ea185e-c6b2-429e-8188-2da297957ad6"
# 

