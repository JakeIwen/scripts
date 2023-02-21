#! /bin/bash

FILE_EXTENSIONS=(mkv avi mp4 rar)
media_group_links() {
  loc="$1"
  folder="$2"
  subfolder="$3"
  
  echo "mgr $folder"
  ls $folder
  keys="multi|REQ|Hi10p|ETRG|YTM_AM|SKGTV|UNCENSORED|HDR|CaLLiOpeD|ddpatmos|CtrlHD|Will1869|10_?Bit|DTS|DL|SDC|Atmos|hdtv|EVO|WiKi|HMAX|IMAX|MA|VhsRip|HDRip|BDRip|iNTERNAL|True_HD|1080p|1080i|720p|XviD|HD|AC3|AAC|REPACK|AAC?5_1|AAC?2_0|REMUX|PRiCK|AVC|HC|AMZN|HEVC|Blu(R|r)ay|(BR|web)(Rip)?|NF|DDP?(5_1|2_0)?|(x|h|X|H)_?26[4-5]|\d+mb|\d+kbps"
  groups="d3g|CiNEFiLE|CTR|PRoDJi|regret|deef|POIASD|Cinefeel|NTG|NTb|monkee|YELLOWBiRD|Atmos|EPSiLON|cielos|ION10|MeGusta|METCON|x0r|xlf|S8RHiNO|GOSSIP|NTG|btx|strife|DD|DBS|TEPES|pawe|ggezl2006|CAKES|HiggsBoson|Coo7"
  delims=" |\.|\+|\-|\,"
  find "$folder" -not -path '*/\.*' -not -ipath '*sample*' -type f -a \( -name '*.mkv' -o -name '*.avi'  -o -name '*.mp4'  -o -name '*.rar' \) | while read pth
  do
    ext="${pth##*.}"
    (( `stat -c%s "$pth"` > 70000000 )) || continue # size > 70MB
    if [[ "$ext" == 'rar' ]] && echo "$handlerars" > /dev/null; then
      handle_rars
      ext="${pth##*.}"
    fi
    no_ext="$(echo "$pth" | perl -pe "s~\.${ext}~~g")"
    no_grp="$(echo $no_ext | perl -pe "s~[\. ]([xh]\.?26[45]|hevc)-\w+(?=(\/|$))~~ig")"
    fmt_pth=`echo $no_grp | perl -pe "s/(${delims})/./g" | perl -pe "s~\.+~_~g" | perl -pe "s~\(~[~g" | perl -pe "s~\)~]~g"`
    cln_pth=`echo $fmt_pth | perl -pe "s~_(${keys})(?=(_|\/|$))~~ig"`
    title=`basename "$cln_pth"`
    link_folder=`echo "$loc" | sed "s|\/torrent\/|\/links\/|g"`
    if [ -n "$subfolder" ]; then
      fmt_sub=`echo "$subfolder" | perl -pe 's~ ~_~g'`
      link_folder="$link_folder/$fmt_sub"
    fi
    # echo "title: $title"
    [ -d "$link_folder" ] || mkdir "$link_folder"
    ln -sf "$pth" "$link_folder/$title"
  done
}

handle_rars() {
  unset vidfile
  dirn="$(dirname "$pth")"
  found_extracted=`find "$dirn" -type f -size +300M -a \( -name '*.mkv' -o -name '*.avi'  -o -name '*.mp4' \)`
  vidfile="`basename "$found_extracted"`"
  if [ -z "$vidfile" ]; then 
    vidfile="$(unar "$pth" -o "$dirn" -t | grep -Po "\S+\.(mkv|avi|mp4)" | head -1)"
    echo "new vidfile: $vidfile"
  else
    echo "vidfile $vidfile"
  fi
  # delete rars files older than 90 days
  find "$dirn/" -type f -mtime +90 -not -name "*$vidfile" -delete 
  pth="$dirn/$vidfile"
}

alias_folders() {
  src=$1
  echo "start $src"
  links="$src/links"
  rm -rf "$links"
  mkdir "$links"
  mkdir "$links/TV" "$links/Documentaries" "$links/Movies" "$links/New" "$links/incomplete"
  unset handlerars
  alias_new "$src"
  handlerars=true
  loc="$src/torrent/TV"
  find "$loc" -maxdepth 1 -mindepth 1  -type d \
    | while read pth; do media_group_links "$loc" "$pth" "$(basename "$pth")"; done
  echo TV
  loc="$src/torrent/Documentaries"
  find "$loc" -maxdepth 2 -mindepth 1  -type d \
    | while read pth; do media_group_links "$loc" "$pth"; done
  echo Docu
  loc="$src/torrent/Movies"
  media_group_links "$loc" "$loc"
  echo Movies
  chmod -R 777 "$links"
  echo "done $src"
}

alias_new() {
  src=$1
  echo "start $src"
  links_inc="$src/links/incomplete"
  links_new="$src/links/New"
  rm -rf "$links_new" "$links_inc"
  mkdir "$links_new" "$links_inc"
  unset handlerars
  loc="$src/torrent/incomplete"
  media_group_links "$loc" "$loc"
  handlerars=true
  loc="$src/torrent/New"
  media_group_links "$loc" "$loc"
  
  chmod -R 777 "$links_new" "$links_inc"
  echo "done $src"
}



# pkill -f alias_media
# if [[ "$(pgrep alias_media)" ]]; then exit 0; fi

touch /home/pi/log/alias_media.log
echo  "$(date): running alias_media.sh $0 $(whoami)" >> /home/pi/scripts/alias_media.log
if [[ "$#" = "1" ]]; then
  mount | grep movingparts && alias_new "/mnt/movingparts" &
else
  mount | grep bigboi && alias_folders "/mnt/bigboi/mp_backup" &
  mount | grep movingparts && alias_folders "/mnt/movingparts" &
fi
echo "alias media end $(date)" >> /home/pi/log/alias_media.log


