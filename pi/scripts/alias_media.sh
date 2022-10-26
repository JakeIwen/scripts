#! /bin/bash

FILE_EXTENSIONS=(mkv avi mp4 rar)
media_group_links() {
  folder="$1"
  regex="$2"
  keys="${regex}multi|REQ|Hi10p|ETRG|YTM\.AM|SKGTV|CaLLiOpeD|CtrlHD|Will1869|10\.?Bit|DTS|DL|SDC|Atmos|hdtv|EVO|WiKi|HMAX|IMAX|MA|VhsRip|HDRip|BDRip|iNTERNAL|True\.HD|1080p|1080i|720p|XviD|HD|AC3|AAC|REPACK|5\.1|2\.0|REMUX|PRiCK|AVC|HC|AMZN|HEVC|Blu(R|r)ay|(BR|web)(Rip)?|NF|DDP?(5\.1|2\.0)?|(x|h|X|H)\.?26[4-5]|\d+mb|\d+kbps"
  groups="d3g|CiNEFiLE|CTR|PRoDJi|regret|deef|POIASD|Cinefeel|NTG|NTb|monkee|YELLOWBiRD|Atmos|EPSiLON|cielos|ION10|MeGusta|METCON|x0r|xlf|S8RHiNO|GOSSIP|NTG|btx|strife|DD|DBS|TEPES|pawe|ggezl2006"
  delims="\.|\+|\-"
  find "$folder" -not -path '*/\.*' -not -ipath '*sample*' -type f | while read pth
  do
    [ -e "$pth" ] || continue 
    ext="${pth##*.}"
    [[ "${FILE_EXTENSIONS[*]}" =~ $ext ]] || continue # wrong extension
    # (( `stat -c%s "$pth"` > 70000000 )) || continue # size > 70MB
    (( `stat -c%s "$pth"` > 10000000 )) || continue # size > 10MB
    echo "$handlerars" > /dev/null && handle_rars
    pattern="($delims)(\[?($keys)\]?(?=\.)|(($groups)\.)?\.?$ext$)|\'"
    title=`basename "$pth" | perl -pe "s/(-| |,)/./g" | perl -pe "s~$pattern~~ig" | perl -pe "s~\.+~_~g" | perl -pe "s~\(~[~g" | perl -pe "s~\)~]~g"`
    link_folder=`echo "$folder" | sed "s|\/torrent\/|\/links\/|g" | perl -pe "s~( |\.)+~_~g"`
    link="$link_folder/$title"
    
    [ -d "$link_folder" ] || mkdir "$link_folder"
    ln -sf "$pth" "$link"
  done
}

handle_rars() {
  if [[ $ext == 'rar' ]]; then
    unset vidfile
    dirn="$(dirname "$pth")"
    vidfile="`basename "$(find "$dirn" -type f -size +300M)"`"
    if [ -z "$vidfile" ]; then 
      vidfile="$(unar "$pth" -o "$dirn" -t | grep -Po "\S+\.(mkv|avi|mp4)" | head -1)"
      echo "new vidfile: $vidfile"
    fi
    find "$dirn/" -type f -mtime +90 -not -name "*$vidfile"
    find "$dirn/" -type f -mtime +90 -not -name "*$vidfile" -delete 
    ext="${vidfile##*.}"
    pth="$dirn/$vidfile"
  fi
}

alias_folders() {
  src=$1
  echo "start $src"
  links="$src/links"
  rm -rf "$links"
  mkdir "$links"
  unset handlerars
  find "$src/torrent/incomplete" -maxdepth 2 -mindepth 1 -type d \
    | while read pth; do media_group_links "$pth"; done
  echo incomplete
  handlerars=true
  mkdir "$links/TV" "$links/Documentaries" "$links/Movies" "$links/New" "$links/incomplete"
  find "$src/torrent/New" -maxdepth 2 -mindepth 1  -type d \
    | while read pth; do media_group_links "$pth"; done
  echo New
  find "$src/torrent/TV" -maxdepth 1 -mindepth 1  -type d \
    | while read pth; do media_group_links "$pth"; done
  echo TV
  find "$src/torrent/Documentaries" -maxdepth 2 -mindepth 1  -type d \
    | while read pth; do media_group_links "$pth"; done
  echo Docu
  media_group_links "$src/torrent/Movies"
  echo Movies
  chmod -R 777 "$links"
  echo "done $src"
}

alias_new() {
  src=$1
  echo "start $src"
  links="$src/links/New"
  rm -rf "$links"
  mkdir "$links"
  unset handlerars
  find "$src/torrent/incomplete" -maxdepth 2 -mindepth 1 -type d \
    | while read pth; do media_group_links "$pth"; done
  echo incomplete
  handlerars=true
  find "$src/torrent/New" -maxdepth 2 -mindepth 1  -type d \
    | while read pth; do media_group_links "$pth"; done
  echo New
  chmod -R 777 "$links"
  echo "done $src"
}

# pkill -f alias_media
# if [[ "$(pgrep alias_media)" ]]; then exit 0; fi

touch /home/pi/log/alias_media.log
echo  "$(date): running alias_media.sh $0 $(whoami)" >> /home/pi/scripts/alias_media.log

mount | grep bigboi && alias_folders "/mnt/bigboi/mp_backup" &
mount | grep movingparts && alias_folders "/mnt/movingparts"
echo "alias media end $(date)" >> /home/pi/log/alias_media.log


