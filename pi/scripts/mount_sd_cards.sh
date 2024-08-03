#!/bin/bash
# 2017-05-06
# 2018-11-18

BOOT_MOUNT='/mnt/SDA1'
ROOT_MOUNT='/mnt/SDA2'

# Check/create Mount Points
if [ ! -e $BOOT_MOUNT ]; then
    mkdir $BOOT_MOUNT
fi
if [ ! -e $ROOT_MOUNT ]; then
    mkdir $ROOT_MOUNT
fi
    echo "mounts " $BOOT_MOUNT  $ROOT_MOUNT
if [ -e /dev/sda ]; then
    SD1='/dev/sda1'
    SD2='/dev/sda2'
else
    SD1='/dev/sdb1'
    SD2='/dev/sdb2'
fi
echo $SD
# Mount Partitions
if ! $(mountpoint -q $BOOT_MOUNT); then
    sudo mount $SD1 $BOOT_MOUNT  # mount partition containing boot files
fi
if ! $(mountpoint -q $ROOT_MOUNT); then
    sudo mount $SD2 $ROOT_MOUNT  # mount root partition containing OS files
fi