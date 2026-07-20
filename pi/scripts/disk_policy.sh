#!/bin/bash
# Filesystem/partition labels that mount_disks.sh reconciles while disk policy
# is enabled.  These labels are also the allowlist for stale-mount recovery.
MOUNT_LABELS=(
  movingparts
  mbp1tbkup
  mbp2tbkup
  hfs2tb
  usbext
  EXFAT512
)

# Filesystem labels for rotational USB disks that must be unmounted and
# explicitly spun down while the van is running.  A newly attached disk is
# intentionally ignored until its label is added here.
HDD_LABELS=(
  movingparts
  bigboi
  mbp2tbkup
)
