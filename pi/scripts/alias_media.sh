#! /bin/bash

FILE_EXTENSIONS=(mkv avi mp4 r00)

media_group_links() {
  regex="$2"
  folder="$1"
  keys="${regex}multi|REQ|ETRG|YTM\.AM|SKGTV|CaLLiOpeD|CtrlHD|Will1869|10\.?Bit|DTS|DL|SDC|Atmos|hdtv|EVO|WiKi|HMAX|IMAX|MA|VhsRip|HDRip|BDRip|DD|iNTERNAL|True\.HD|1080p|1080i|720p|XviD|HD|AC3|AAC|REPACK|5\.1|2\.0|REMUX|PRiCK|AVC|HC|AMZN|HEVC|Bluray|(BR|web)(Rip)?|NF|DDP?(5\.1|2\.0)?|(x|h)\.?26[4-5]"
  groups="d3g|CiNEFiLE|PRoDJi|regret|POIASD|Cinefeel|NTG|NTb|monkee|YELLOWBiRD|Atmos|EPSiLON|cielos|ION10|MeGusta|METCON|x0r|xlf|S8RHiNO|NTG|btx|strife|DD|DBS|TEPES|pawel2006"
  delims="\.|\+|\-"
  find "$folder" -not -path '*/\.*' -not -ipath '*sample*' -type f | while read pth
  do
    [ -e "$pth" ] || continue 
    ext="${pth##*.}"
    [[ "${FILE_EXTENSIONS[*]}" =~ $ext ]] || continue # wrong extension
    (( `stat -c%s "$pth"` > 70000000 )) || continue # size > 70MB
    pattern="($delims)(\[?($keys)\]?(?=\.)|(($groups)\.)?\.?$ext$)|\'"
    # echo $pattern
    # echo "bname: `basename "$pth" | perl -pe "s/(-| |,)/./g"`"
    title=`basename "$pth" | perl -pe "s/(-| |,)/./g" | perl -pe "s~$pattern~~ig" | perl -pe "s~\.+~_~g"`
    # echo $title
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
  mkdir "$links" "$links/TV" "$links/Documentaries" "$links/Movies"
 
  find "$src/torrent/TV" -maxdepth 1 -mindepth 1  -type d \
    | while read pth; do media_group_links "$pth"; done
  find "$src/torrent/Documentaries" -maxdepth 1 -mindepth 1  -type d \
    | while read pth; do media_group_links "$pth"; done
  media_group_links "$src/torrent/Movies"
  
  chmod -R 777 "$links"
}

[ -e "/mnt/movingparts" ] && alias_folders "/mnt/movingparts"
[ -e "/mnt/bigboi/mp_backup" ] && alias_folders "/mnt/bigboi/mp_backup"


# mnt/movingparts
# find . -type l -exec cp --parents {} ../links \;
