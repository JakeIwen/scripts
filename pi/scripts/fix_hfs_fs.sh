#! /bin/bash
grep 'hfsplus ro,' /proc/mounts | while read -r mnt_spec; do
  echo "fixing read-only hfs mount: $mnt_spec"
  mnt=`echo $mnt_spec | cut -d ' ' -f1`
  loc=`echo $mnt_spec | cut -d ' ' -f2`
  sudo fsck.hfsplus $mnt
  sudo umount $mnt
  sudo mount $mnt -t hfsplus -o force,rw $loc 
  new_status=`grep "$loc" /proc/mounts`
  echo "new status: $new_status"
done




