#! /bin/bash
sudo mount -U "a5204e11-ade6-3f5b-ac60-316848b2e72b" -t "hfsplus" /mnt/applehfs && echo "mounted hfs"
sudo mount -U "b3f3432a-57a0-4fb7-bb54-db528d15bca7" -t "ext4" /mnt/seegayte && echo "mounted sg"
sudo mount -U "40c2277a-91f3-440f-a9cb-8417e9d64e03" -t "ext4" /mnt/movingparts && echo "mounted mp"
sudo mount -U "eea5b6a8-94a3-3fb8-99d8-3015bbafe289" -t "hfsplus" /mnt/usbhfs && echo "mounted usb"

