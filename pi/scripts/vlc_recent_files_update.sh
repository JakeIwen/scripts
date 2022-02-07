#!/bin/bash

recpath="$HOME/vlc-recent.txt"
l=`grep -Po  '(?<=list=).*?(?=, )' "$HOME/.config/vlc/vlc-qt-interface.conf"`
title="$(basename "${l%.*}")"
dir="$(dirname "$l")"
dir2=`echo "$dir" | rev | cut -d '/' -f2 | rev`
filename="$(echo "$dir2--$title" | sed 's|\%20|_|g')" # | perl -pe "s~$keys~~g"
sed -i "/$filename/d" $recpath
sed -i "1i $filename" $recpath
sed -i "/nohup/d" $recpath
echo "file: $filename"
