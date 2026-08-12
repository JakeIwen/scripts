#!/bin/bash

FILE_EXTENSIONS=(mkv avi mp4 rar)

validate_source() {
  case "$1" in
    /mnt/movingparts | /mnt/bigboi/mp_backup) return 0 ;;
    *) echo "refusing unexpected media source: $1" >&2; return 1 ;;
  esac
}

source_is_mounted() {
  validate_source "$1" || return 1
  case "$1" in
    /mnt/movingparts)
      /usr/bin/mountpoint -q /mnt/movingparts
      ;;
    /mnt/bigboi/mp_backup)
      # mp_backup is a directory on the bigboi filesystem, not itself a mount.
      /usr/bin/mountpoint -q /mnt/bigboi && [[ -d /mnt/bigboi/mp_backup ]]
      ;;
  esac
}

media_group_links() {
  local loc="$1"
  local folder="$2"
  local subfolder="${3:-}"
  local output_folder="${4:-}"
  local pth ext size no_apos no_ext no_and no_brk fmt_pth no_grp cln_pth
  local title link_folder fmt_sub
  local keys groups delims
  local listing failed=0

  [[ -d "$folder" ]] || return 0
  
  keys="multi|PROPER|iP|REQ|FGT|EAC3|SANTi|MutzNutz|ViSiON|POOTLED|COLLECTiVE|TELESYNC|Hi10p|ETRG|YTM_AM|SKGTV|HDR10|UNCENSORED|HDR|CaLLiOpeD|ddpatmos|CtrlHD|Will1869|10_?Bit|DTS|DL|SDC|hdtv|EVO|WiKi|HMAX|IMAX|MA|VhsRip|HDRip|BDRip|iNTERNAL|True_HD|1080[pi](MAX)?|720p|XviD|HD|AC3|REPACK|REMUX|PRiCK|AVC|HC|AMZN|HULU|1080pWEBRip|Blu(R|r)ay|(BR|web|WEB)(Rip)?|NF|(AAC|DDP?)_?(5_1|2_0)?|\d+mb|\d+kbps|_$"
  groups="d3g|CiNEFiLE|CTR|PRoDJi|regret|deef|POIASD|Cinefeel|NTG|NTb|monkee|YELLOWBiRD|Atmos|EPSiLON|cielos|ION10|MeGusta|METCON|x0r|xlf|S8RHiNO|GOSSIP|btx|strife|DBS|TEPES|pawe|ggezl2006|CAKES|HiggsBoson|Coo7"
  delims=" |\.|\+|\-|\,| "
  listing="$(mktemp "${TMPDIR:-/tmp}/alias-media-files.XXXXXX")" || return 1
  if ! find "$folder" -not -path '*/\.*' -not -ipath '*sample*' -type f -a \( -name '*.mkv' -o -name '*.avi'  -o -name '*.mp4'  -o -name '*.rar' \) -print0 >"$listing"; then
    rm -f -- "$listing"
    return 1
  fi
  while IFS= read -r -d '' pth
  do
    ext="${pth##*.}"
    size="$(stat -c%s -- "$pth")" || { failed=1; continue; }
    (( size > 70000000 )) || continue # size > 70MB
    if [[ "$ext" == 'rar' && "${handlerars:-}" == true ]]; then
      handle_rars || { failed=1; continue; }
      ext="${pth##*.}"
    fi
    no_apos="$(echo "$pth" | perl -pe "s~'~~g")"
    no_ext="$(echo "$no_apos" | perl -pe "s~\.${ext}~~g")"
    no_and="$(echo "$no_ext" | perl -pe "s~\&~and~g")"
    no_brk="$(echo "$no_and" | perl -pe "s~\[|\]|\(|\)~~g")"
    fmt_pth="$(echo "$no_brk" | perl -pe "s/(${delims})/./g" | perl -pe "s~\.+~_~g")"
    no_grp="$(echo "$fmt_pth" | perl -pe "s~_([xh]_?26[45]|hevc)(_\w+)?(?=(\/|$))~~ig")"
    cln_pth="$(echo "$no_grp" | perl -pe "s~_(${keys})(?=(_|\/|$))~~ig")"
    cln_pth="$(echo "$cln_pth" | perl -pe "s~_(${groups})(?=(_|\/|$))~~ig")"
    
    title="$(basename "$cln_pth")"
    link_folder="${output_folder:-$(echo "$loc" | sed "s|\/torrent\/|\/links\/|g")}"
    if [ -n "$subfolder" ]; then
      fmt_sub="$(echo "$subfolder" | perl -pe 's~ ~_~g')"
      link_folder="$link_folder/$fmt_sub"
    fi
    # echo "title: $title"
    mkdir -p -- "$link_folder" || { failed=1; continue; }
    ln -sf -- "$pth" "$link_folder/$title" || failed=1
  done <"$listing"
  rm -f -- "$listing"
  return "$failed"
}

handle_rars() {
  local origdir dirn found_extracted vidfile
  origdir="$(pwd)"
  dirn="$(dirname "$pth")"
  found_extracted="$(find "$dirn" -type f -size +300M -a \( -name '*.mkv' -o -name '*.avi'  -o -name '*.mp4' \) -print -quit)"
  
  if [ -z "$found_extracted" ]; then 
    echo "extracting archive $pth"
    cd "$dirn" || return 1
    unrar x -r -inul "$pth"
    found_extracted="$(find "$dirn" -type f -size +300M -a \( -name '*.mkv' -o -name '*.avi'  -o -name '*.mp4' \) -print -quit)"
    if [ -z "$found_extracted" ]; then
      printf '\nERROR: did not find vidfile (>300M, mkv/avi/mp4) after extracting archive\n\n' >&2
      cd "$origdir" || true
      return 1
    fi
  else
    echo "found existing extracted file!"
  fi
  vidfile="$(basename "$found_extracted")"
  echo "vidfile $vidfile"
  # Torrent payloads are immutable seed data.  Never remove archives here;
  # extraction cleanup belongs in a separate derived-media cache.
  pth="$dirn/$vidfile"
  cd "$origdir" || exit
}

alias_folders() {
  local src="$1"
  local stage old links loc pth listing
  validate_source "$src" || return 1
  echo "start $src"
  stage="$(mktemp -d "$src/.links-stage.XXXXXX")" || return 1
  links="$src/links"
  old="$src/.links-old.$$"
  mkdir -p -- "$stage/TV" "$stage/Documentaries" "$stage/Movies" \
    "$stage/New" "$stage/incomplete" || { rm -rf -- "$stage"; return 1; }

  if ! alias_new_into "$src" "$stage"; then
    rm -rf -- "$stage"
    return 1
  fi
  handlerars=true
  loc="$src/torrent/TV"
  if [[ -d "$loc" ]]; then
    listing="$(mktemp "${TMPDIR:-/tmp}/alias-media-dirs.XXXXXX")" || {
      rm -rf -- "$stage"
      return 1
    }
    if ! find "$loc" -maxdepth 1 -mindepth 1 -type d -print0 >"$listing"; then
      rm -f -- "$listing"
      rm -rf -- "$stage"
      return 1
    fi
    while IFS= read -r -d '' pth; do
      media_group_links "$loc" "$pth" "$(basename "$pth")" "$stage/TV" || {
        rm -f -- "$listing"
        rm -rf -- "$stage"
        return 1
      }
    done <"$listing"
    rm -f -- "$listing"
  fi
  echo TV
  loc="$src/torrent/Documentaries"
  if [[ -d "$loc" ]]; then
    listing="$(mktemp "${TMPDIR:-/tmp}/alias-media-dirs.XXXXXX")" || {
      rm -rf -- "$stage"
      return 1
    }
    if ! find "$loc" -maxdepth 2 -mindepth 1 -type d -print0 >"$listing"; then
      rm -f -- "$listing"
      rm -rf -- "$stage"
      return 1
    fi
    while IFS= read -r -d '' pth; do
      media_group_links "$loc" "$pth" "" "$stage/Documentaries" || {
        rm -f -- "$listing"
        rm -rf -- "$stage"
        return 1
      }
    done <"$listing"
    rm -f -- "$listing"
  fi
  echo Docu
  loc="$src/torrent/Movies"
  media_group_links "$loc" "$loc" "" "$stage/Movies" || {
    rm -rf -- "$stage"
    return 1
  }
  echo Movies
  chmod -R 777 "$stage" || { rm -rf -- "$stage"; return 1; }

  if [[ -e "$old" || -L "$old" ]]; then
    echo "refusing stale full-library rollback path: $old" >&2
    rm -rf -- "$stage"
    return 1
  fi
  if [[ -e "$links" || -L "$links" ]]; then
    mv -- "$links" "$old" || { rm -rf -- "$stage"; return 1; }
  fi
  if ! mv -- "$stage" "$links"; then
    [[ -e "$old" || -L "$old" ]] && mv -- "$old" "$links"
    return 1
  fi
  rm -rf -- "$old"
  echo "done $src"
}

alias_new_into() {
  local src="$1"
  local output_root="$2"
  local loc
  mkdir -p -- "$output_root/New" "$output_root/incomplete" || return 1
  unset handlerars
  loc="$src/torrent/incomplete"
  media_group_links "$loc" "$loc" "" "$output_root/incomplete" || return 1
  handlerars=true
  loc="$src/torrent/New"
  media_group_links "$loc" "$loc" "" "$output_root/New" || return 1
}

alias_new() {
  local src="$1"
  local links_inc links_new stage stage_inc stage_new old_inc old_new
  validate_source "$src" || return 1
  echo "start $src"
  mkdir -p -- "$src/links" || return 1
  links_inc="$src/links/incomplete"
  links_new="$src/links/New"
  stage="$(mktemp -d "$src/links/.alias-new.XXXXXX")" || return 1
  if ! alias_new_into "$src" "$stage"; then
    rm -rf -- "$stage"
    return 1
  fi
  stage_inc="$stage/incomplete"
  stage_new="$stage/New"
  chmod -R 777 "$stage_new" "$stage_inc" || {
    rm -rf -- "$stage"
    return 1
  }
  old_inc="$src/links/.incomplete.old.$$"
  old_new="$src/links/.New.old.$$"
  if [[ -e "$old_inc" || -L "$old_inc" || -e "$old_new" || -L "$old_new" ]]; then
    echo "refusing stale incremental rollback paths under $src/links" >&2
    rm -rf -- "$stage"
    return 1
  fi
  if [[ -e "$links_inc" || -L "$links_inc" ]]; then
    mv -- "$links_inc" "$old_inc" || { rm -rf -- "$stage"; return 1; }
  fi
  if [[ -e "$links_new" || -L "$links_new" ]]; then
    if ! mv -- "$links_new" "$old_new"; then
      [[ -e "$old_inc" || -L "$old_inc" ]] && mv -- "$old_inc" "$links_inc"
      rm -rf -- "$stage"
      return 1
    fi
  fi
  if ! mv -- "$stage_inc" "$links_inc" || ! mv -- "$stage_new" "$links_new"; then
    rm -rf -- "$links_inc" "$links_new"
    [[ -e "$old_new" || -L "$old_new" ]] && mv -- "$old_new" "$links_new"
    [[ -e "$old_inc" || -L "$old_inc" ]] && mv -- "$old_inc" "$links_inc"
    rm -rf -- "$stage"
    return 1
  fi
  rmdir "$stage" 2>/dev/null || true
  rm -rf -- "$old_inc" "$old_new"
  echo "done $src"
}

notify_video_library() {
  /usr/bin/curl -fsS --connect-timeout 1 --max-time 2 \
    -H 'X-Van-Video: 1' -X POST \
    http://127.0.0.1:8789/api/torrents/reconcile >/dev/null 2>&1 || true
}

run_locked() {
  local mode="$1"
  local status=0
  local ran=false
  local -a jobs=()

  exec 9>/run/lock/alias-media.lock || return 1
  # The caller has already detached this worker, so waiting here never blocks
  # qBittorrent.  Queueing each request avoids the lost-wakeup race of a
  # nonblocking flock while the previous scan is about to publish its index.
  /usr/bin/flock 9 || return 1

  echo "$(date): running alias_media.sh $mode as $(whoami)"
  if [[ "$mode" == new ]]; then
    if source_is_mounted /mnt/movingparts; then
      ran=true
      alias_new "/mnt/movingparts" || status=1
    fi
  else
    if source_is_mounted /mnt/bigboi/mp_backup; then
      ran=true
      alias_folders "/mnt/bigboi/mp_backup" &
      jobs+=("$!")
    fi
    if source_is_mounted /mnt/movingparts; then
      ran=true
      alias_folders "/mnt/movingparts" &
      jobs+=("$!")
    fi
    for job in "${jobs[@]}"; do
      wait "$job" || status=1
    done
  fi

  if [[ "$ran" == true && "$status" -eq 0 ]]; then
    notify_video_library
  fi
  echo "alias media end $(date), status=$status"
  return "$status"
}

main() {
  local mode=full
  if [[ "$#" -gt 1 || ( "$#" -eq 1 && "$1" != new ) ]]; then
    echo "usage: $0 [new]" >&2
    return 2
  fi
  [[ "$#" -eq 1 ]] && mode=new

  mkdir -p -- /home/pi/log
  # qBittorrent's completion command must return immediately.  The detached
  # worker has no inherited stdin/stdout pipe, uses the same non-waiting lock
  # for full and incremental runs, and bounds its loopback notification.
  (
    run_locked "$mode"
  ) </dev/null >>/home/pi/log/alias_media.log 2>&1 &
  disown "$!" 2>/dev/null || true
  echo "alias media $mode scheduled"
}

main "$@"
