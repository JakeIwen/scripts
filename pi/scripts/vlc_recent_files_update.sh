#!/bin/bash

recpath="$HOME/vlc-recent.txt"
vlc_recents_path="$HOME/.config/vlc/vlc-qt-interface.conf"
for l in `grep 'list=' $vlc_recents_path`; do
  filename=`echo $l | rev | cut -d '/' -f1 | rev | sed 's|,||g'`
  sed -i "/$filename/d" $recpath
  sed -i "1i $filename" $recpath
done

