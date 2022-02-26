#! /bin/bash

# pkill -f alias_media
# if [[ "$(pgrep alias_media)" ]]; then exit 0; fi

echo  "$(date): running alias_media.sh $0 $(whoami)" >> /home/pi/scripts/alias_media.log

FILE_EXTENSIONS=(mkv avi mp4 rar)

media_group_links() {
  regex="$2"
  folder="$1"
  keys="${regex}multi|REQ|Hi10p|ETRG|YTM\.AM|SKGTV|CaLLiOpeD|CtrlHD|Will1869|10\.?Bit|DTS|DL|SDC|Atmos|hdtv|EVO|WiKi|HMAX|IMAX|MA|VhsRip|HDRip|BDRip|iNTERNAL|True\.HD|1080p|1080i|720p|XviD|HD|AC3|AAC|REPACK|5\.1|2\.0|REMUX|PRiCK|AVC|HC|AMZN|HEVC|Blu(R|r)ay|(BR|web)(Rip)?|NF|DDP?(5\.1|2\.0)?|(x|h|X|H)\.?26[4-5]|\d+mb|\d+kbps"
  groups="d3g|CiNEFiLE|CTR|PRoDJi|regret|deef|POIASD|Cinefeel|NTG|NTb|monkee|YELLOWBiRD|Atmos|EPSiLON|cielos|ION10|MeGusta|METCON|x0r|xlf|S8RHiNO|NTG|btx|strife|DD|DBS|TEPES|pawel2006"
  delims="\.|\+|\-"
  find "$folder" -not -path '*/\.*' -not -ipath '*sample*' -type f | while read pth
  do
    [ -e "$pth" ] || continue 
    ext="${pth##*.}"
    [[ "${FILE_EXTENSIONS[*]}" =~ $ext ]] || continue # wrong extension
    (( `stat -c%s "$pth"` > 70000000 )) || [[ $ext == 'r00' ]] || continue # size > 70MB
    pattern="($delims)(\[?($keys)\]?(?=\.)|(($groups)\.)?\.?$ext$)|\'"
    title=`basename "$pth" | perl -pe "s/(-| |,)/./g" | perl -pe "s~$pattern~~ig" | perl -pe "s~\.+~_~g" | perl -pe "s~\(~[~g" | perl -pe "s~\)~]~g"`
    link_folder=`echo "$folder" | sed "s|\/torrent\/|\/links\/|g" | perl -pe "s~( |\.)+~_~g"`
    link="$link_folder/$title"
    
    [ -d "$link_folder" ] || mkdir "$link_folder"
    ln -s "$pth" "$link"
  done
}

cargs(){
  ARGS=("$@")
  echo "${ARGS[@]}"
}

alias_folders() {
  src=$1
  links="$src/links"
  rm -rf "$links" || True
  mkdir "$links" "$links/TV" "$links/Documentaries" "$links/Movies" "$links/New" "$links/incomplete"
  find "$src/torrent/incomplete" -maxdepth 2 -mindepth 1  -type d \
    | while read pth; do media_group_links "$pth"; done
  echo incomplete
  find "$src/torrent/TV" -maxdepth 1 -mindepth 1  -type d \
    | while read pth; do media_group_links "$pth"; done
  echo TV
  find "$src/torrent/Documentaries" -maxdepth 1 -mindepth 1  -type d \
    | while read pth; do media_group_links "$pth"; done
  echo Docu
  find "$src/torrent/New" -maxdepth 2 -mindepth 1  -type d \
    | while read pth; do media_group_links "$pth"; done
  echo New
  media_group_links "$src/torrent/Movies"
  echo Movies
  chmod -R 777 "$links"
}
touch /home/pi/log/alias_media.log
echo "alias media begin $(date)" >> /home/pi/log/alias_media.log
echo "mp start"
[ -e "/mnt/movingparts" ] && alias_folders "/mnt/movingparts"
echo "mp done; bb start"
[ -e "/mnt/bigboi/mp_backup" ] && alias_folders "/mnt/bigboi/mp_backup"
echo "bb done"
echo "alias media end $(date)" >> /home/pi/log/alias_media.log

# mnt/movingparts
# find . -type l -exec cp --parents {} ../links \;
