#!/bin/bash
# Filesystem labels for rotational USB disks that must be unmounted and
# explicitly spun down while the van is running.  A newly attached disk is
# intentionally ignored until its label is added here.
HDD_LABELS=(
  movingparts
  bigboi
  mbp2tbkup
)
