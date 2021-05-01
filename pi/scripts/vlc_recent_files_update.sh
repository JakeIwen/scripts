#!/bin/bash

recpath="$HOME/vlc-recent.txt"
l=`grep -Po  '(?<=list=).*?(?=, )' "$HOME/.config/vlc/vlc-qt-interface.conf"`
title="$(basename "${l%.*}")"
dir="$(dirname "$l")"
dir1=`echo "$dir" | rev | cut -d '/' -f1 | rev`
dir2=`echo "$dir" | rev | cut -d '/' -f2 | rev`
filename="$dir2--$dir1--$title"
if [[ $dir2 == 'torrent' ]]; then filename="$dir1--$title"; fi
filename="$(echo "$filename" | sed 's|\%20|_|g')" # | perl -pe "s~$keys~~g"
sed -i "/$filename/d" $recpath
sed -i "1i $filename" $recpath
echo "$filename"
sed -i "/nohup/d" $recpath
