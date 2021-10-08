#! /bin/bash
sudo mount -U "8790cc1e-e5ca-372e-b846-8cedd282e9bf" -t hfsplus -o force,rw /mnt/mbbackup && echo "mounted mbbackup"
sudo mount -U "eea5b6a8-94a3-3fb8-99d8-3015bbafe289" -t hfsplus -o force,rw /mnt/usbhfs && echo "mounted usb"
sudo mount -U "b3f3432a-57a0-4fb7-bb54-db528d15bca7" -t ext4 -o nofail /mnt/movingparts && echo "mounted mp 2TB"
sudo mount -U "313cb347-2278-42d0-8a2c-f1dde1e82725" -t ext4 -o nofail /mnt/bigboi && echo "mounted bb"
sudo mount -U "40c2277a-91f3-440f-a9cb-8417e9d64e03" -t ext4 -o nofail /mnt/seegayte && echo "mounted seegayte"
sudo mount -U "e138c71d-3cc3-4dfb-acb0-a365af29e9b8" -t ext4 -o nofail /mnt/micro_sd && echo "mounted micro_sd"


