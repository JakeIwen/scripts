#!/bin/bash

recpath="$HOME/vlc-recent.txt"
vlc_recents_path="$HOME/.config/vlc/vlc-qt-interface.conf"
# keys="|Will1869|10\.?Bit|DTS|DL|NTb|ION10|MeGusta|METCON|strife|SDC|hdtv|CiNEFiLE|PRoDJi|EVO|POIASD|WiKi|HMAX|IMAX|MA|VhsRip|x0r|iNTERNAL|True-HD|(1080|720)(p|i)|Xvi|HD|AC3|AAC|REPACK|REMUX|PRiCK|AVC|d3g|Atmos|EPSiLON|HC|AMZN|HEVC|Blu(R|r)ay|(BR|WEB|web)(Rip)?|NF|DDP?\+?|(x|X|H|h)\.?26[4-5]"
list=(`grep 'list=' $vlc_recents_path`)
for l in  "${list[@]}"; do
  title=`basename "${l%.*}"`
  dir=`dirname "$l"`
  [[ $title == 'nohup' ]] && continue
  dir1=`echo "$dir" | rev | cut -d '/' -f1 | rev`
  dir2=`echo "$dir" | rev | cut -d '/' -f2 | rev`
  filename="$dir2--$dir1--$title"
  if [[ $dir2 == 'torrent' ]]; then filename="$dir1--$title"; fi
  filename=`echo "$filename" | sed 's|\%20|_|g' # | perl -pe "s~$keys~~g"`
  sed -i "/$filename/d" $recpath
  sed -i "1i $filename" $recpath
  # echo "$filename" >> $recpath
done
