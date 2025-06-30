#! /bin/bash

FILE_EXTENSIONS=(mkv avi mp4 rar)
media_group_links() {
  loc="$1"
  folder="$2"
  subfolder="$3"
  
  keys="multi|PROPER|iP|REQ|FGT|EAC3|SANTi|MutzNutz|ViSiON|POOTLED|COLLECTiVE|TELESYNC|Hi10p|ETRG|YTM_AM|SKGTV|HDR10|UNCENSORED|HDR|CaLLiOpeD|ddpatmos|CtrlHD|Will1869|10_?Bit|DTS|DL|SDC|hdtv|EVO|WiKi|HMAX|IMAX|MA|VhsRip|HDRip|BDRip|iNTERNAL|True_HD|1080[pi](MAX)?|720p|XviD|HD|AC3|REPACK|REMUX|PRiCK|AVC|HC|AMZN|HULU|1080pWEBRip|Blu(R|r)ay|(BR|web|WEB)(Rip)?|NF|(AAC|DDP?)_?(5_1|2_0)?|\d+mb|\d+kbps|_$"
  groups="d3g|CiNEFiLE|CTR|PRoDJi|regret|deef|POIASD|Cinefeel|NTG|NTb|monkee|YELLOWBiRD|Atmos|EPSiLON|cielos|ION10|MeGusta|METCON|x0r|xlf|S8RHiNO|GOSSIP|btx|strife|DBS|TEPES|pawe|ggezl2006|CAKES|HiggsBoson|Coo7"
  delims=" |\.|\+|\-|\,| "
  find "$folder" -not -path '*/\.*' -not -ipath '*sample*' -type f -a \( -name '*.mkv' -o -name '*.avi'  -o -name '*.mp4'  -o -name '*.rar' \) | while read pth
  do
    ext="${pth##*.}"
    (( `stat -c%s "$pth"` > 70000000 )) || continue # size > 70MB
    if [[ "$ext" == 'rar' ]] && echo "$handlerars" > /dev/null; then
      handle_rars
      ext="${pth##*.}"
    fi
    no_apos="$(echo "$pth" | perl -pe "s~'~~g")"
    no_ext="$(echo "$no_apos" | perl -pe "s~\.${ext}~~g")"
    no_and="$(echo "$no_ext" | perl -pe "s~\&~and~g")"
    no_brk="$(echo "$no_and" | perl -pe "s~\[|\]|\(|\)~~g")"
    fmt_pth=`echo $no_brk | perl -pe "s/(${delims})/./g" | perl -pe "s~\.+~_~g"`
    no_grp="$(echo $fmt_pth | perl -pe "s~_([xh]_?26[45]|hevc)(_\w+)?(?=(\/|$))~~ig")"
    cln_pth=`echo $no_grp | perl -pe "s~_(${keys})(?=(_|\/|$))~~ig"`
    cln_pth=`echo $cln_pth | perl -pe "s~_(${groups})(?=(_|\/|$))~~ig"`
    
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
  origdir="$(pwd)"
  dirn="$(dirname "$pth")"
  found_extracted=`find "$dirn" -type f -size +300M -a \( -name '*.mkv' -o -name '*.avi'  -o -name '*.mp4' \)`
  
  if [ -z "$found_extracted" ]; then 
    echo "extracting archive $pth"
    cd "$dirn" || exit
    unrar x -r -inul "$pth"
    found_extracted=`find "$dirn" -type f -size +300M -a \( -name '*.mkv' -o -name '*.avi'  -o -name '*.mp4' \)`
    if [ -z "$found_extracted" ]; then
      echo "\nERROR: did not find vidfile (>300M, mkv/avi/mp4) after extracting archive\n"
      return
    fi
  else
    echo "found existing extracted file!"
  fi
  vidfile="`basename "$found_extracted"`"
  echo "vidfile $vidfile"
  # delete rars files older than 90 days
  find "$dirn/" -type f -mtime +90 -not -name "*$vidfile" -delete 
  pth="$dirn/$vidfile"
  cd "$origdir" || exit
}

alias_folders() {
  src=$1
  echo "start $src"
  mkdir "$src/links"
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
  mkdir "$src/links"
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
echo  "$(date): running alias_media.sh $0 $(whoami)" 
if [[ "$#" = "1" ]]; then
  mount | grep movingparts && alias_new "/mnt/movingparts" &
else
  mount | grep bigboi && alias_folders "/mnt/bigboi/mp_backup" &
  mount | grep movingparts && alias_folders "/mnt/movingparts" &
fi
echo "alias media end $(date)" >> /home/pi/log/alias_media.log
echo "alias media end $(date)"

