#! /bin/bash

media_group_links() {
  ext=$1
  regex=$2
  keys="${regex}Will1869|10\.?Bit|DTS|HMAX|IMAX|MA|VhsRip|iNTERNAL|True-HD|(1080|720)(p|i)|Xvi|HD|AC3|AAC|REPACK|REMUX|PRiCK|AVC|Atmos|EPSiLON|HC|AMZN|HEVC|Blu(R|r)ay|(BR|WEB)(Rip)?|NF|DDP?\+?|(x|X|H|h)\.?26[4-5]"
  pattern="(\.|-)($keys)(.?(5.1)|(2.0))?-?\w*-?(?=(\.|-))|\[.*\]|$ext$"
  find . -not -path '*/\.*' -not -ipath '*sample*' -type f -iname "*.$ext" | while read pth
  do
    [ -e "$pth" ] || continue
    title=`basename "$pth" | perl -pe "s~$pattern~~g" | perl -pe "s/(-| |,)/./g" | perl -pe "s|\.+|.|g" | perl -pe "s/(.|-)$//g"`
    title="$title.$ext"
    echo $title
    # ln -s $pth $title
  done
}

alias_folder() {
  cd $1 || exit
  find . -type l | while read lk; do rm $lk; done
  media_group_links 'mkv' $2
  media_group_links 'avi' $2
  media_group_links 'mp4' $2
  media_group_links 'r00' $2
}

find '/mnt/movingparts/torrent/TV' -type d -maxdepth 1 -mindepth 1 \
  | while read pth; do alias_folder $pth '\d\d\d\d|'; done
find '/mnt/movingparts/torrent/Documentaries' -type d -maxdepth 1 -mindepth 1 \
  | while read pth; do alias_folder $pth; done
alias_folder '/mnt/movingparts/torrent/Movies'
# mnt/movingparts
# find . -type l -exec cp --parents {} ../links \;
