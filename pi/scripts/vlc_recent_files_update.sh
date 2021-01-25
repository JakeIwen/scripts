#!/bin/bash

recpath="$HOME/vlc-recent.txt"
fpath="$HOME/.config/vlc/vlc-qt-interface.conf"
for l in `grep 'list=' $fpath`; do
  filename=`echo $l | rev | cut -d '/' -f1 | rev | sed 's|,||g'`
  sed -i "/$filename/d" $recpath
  sed -i "1i $filename" $recpath
done