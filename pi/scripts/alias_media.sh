#! /bin/bash

media_group_links() {
  ext="$1"
  regex="$2"
  folder="$3"
  keys="${regex}Will1869|10\.?Bit|DTS|DL|NTb|ION10|MeGusta|METCON|strife|SDC|hdtv|CiNEFiLE|PRoDJi|EVO|POIASD|WiKi|HMAX|IMAX|MA|VhsRip|x0r|iNTERNAL|True-HD|(1080|720)(p|i)|Xvi|HD|AC3|AAC|REPACK|REMUX|PRiCK|AVC|d3g|Atmos|EPSiLON|HC|AMZN|HEVC|Blu(R|r)ay|(BR|WEB|web)(Rip)?|NF|DDP?\+?|(x|X|H|h)\.?26[4-5]"
  pattern="(\.|-)($keys)(.?(5.1)|(2.0))?-?\w*-?(?=(\.|-))|\[.*\]|$ext$"
  find "$folder" -not -path '*/\.*' -not -ipath '*sample*' -type f -iname "*.$ext" | while read pth
  do
    [ -e "$pth" ] || continue 
    (( `stat -c%s "$pth"` > 70000000 )) || continue # size > 70MB
    
    title=`basename "$pth" | perl -pe "s/(-| |,)/./g" | perl -pe "s~$pattern~~g" | perl -pe "s|\.+|.|g" | perl -pe "s/(\.|-)$//g"`
    link_folder=`echo "$folder" | sed "s|\/torrent\/|\/links\/|g"`
    link="$link_folder/$title.$ext"

    [ -d "$link_folder" ] || mkdir "$link_folder"
    ln -s "$pth" "$link"
  done
}

alias_folder() {
  media_group_links 'mkv' "$2" "$1"
  media_group_links 'avi' "$2" "$1"
  media_group_links 'mp4' "$2" "$1"
  media_group_links 'r00' "$2" "$1"
}

prep_dir() {
  links='/mnt/movingparts/links'
  rm -rf "$links" || True
  mkdir "$links"
  mkdir "$links/TV"
  mkdir "$links/Documentaries"
  mkdir "$links/Movies"
}

prep_dir

find '/mnt/movingparts/torrent/TV' -maxdepth 1 -mindepth 1  -type d \
  | while read pth; do alias_folder "$pth" '\d\d\d\d|'; done
find '/mnt/movingparts/torrent/Documentaries' -maxdepth 1 -mindepth 1  -type d \
  | while read pth; do alias_folder "$pth"; done
alias_folder '/mnt/movingparts/torrent/Movies'
# mnt/movingparts
# find . -type l -exec cp --parents {} ../links \;
