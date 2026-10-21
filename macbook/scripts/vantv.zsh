#!/bin/zsh
# vantv: cast a YouTube video from the Mac to Van TV, the Pi's ad-free cast
# receiver (pi/projects/youtube-cast), over its LAN status server. The Firefox
# extension (macbook/firefox/van-tv) speaks the same /api/cast and /api/control.
set -euo pipefail

VAN_TV=${VAN_TV:-http://vanpi.lan:8795}

usage() {
  print -u2 'usage: vantv [youtube-url]   cast a link (default: front Chrome/Safari YouTube tab, then clipboard)'
  print -u2 '       vantv pause|resume|stop|status'
  exit 2
}

post() {
  curl -fsS -m 10 -X POST -H 'Content-Type: application/json' -d "$2" "$VAN_TV/api/$1"
  print
}

is_youtube() { [[ $1 =~ '^https://(((www|m|music)\.)?youtube\.com|youtu\.be)/' ]] }

# Prints "<id> <start seconds>" for watch, youtu.be, shorts, live and embed links.
video_id() {
  local url=$1 id='' start=0
  if [[ $url =~ '[?&]v=([A-Za-z0-9_-]{11})' ]]; then id=$match[1]
  elif [[ $url =~ 'youtu\.be/([A-Za-z0-9_-]{11})' ]]; then id=$match[1]
  elif [[ $url =~ '/(shorts|live|embed)/([A-Za-z0-9_-]{11})' ]]; then id=$match[2]
  fi
  [[ -n $id ]] || return 1
  [[ $url =~ '[?&#](t|start)=([0-9]+)s?' ]] && start=$match[2]
  print -r -- "$id $start"
}

front_tab_url() {
  local url
  if pgrep -xq 'Google Chrome'; then
    url=$(osascript -e 'tell application "Google Chrome" to get URL of active tab of front window' 2>/dev/null || true)
    is_youtube "$url" && { print -r -- "$url"; return }
  fi
  if pgrep -xq Safari; then
    url=$(osascript -e 'tell application "Safari" to get URL of current tab of front window' 2>/dev/null || true)
    is_youtube "$url" && { print -r -- "$url"; return }
  fi
  url=$(pbpaste)
  is_youtube "$url" && { print -r -- "$url"; return }
  print -u2 'No YouTube link given, in the front Chrome/Safari tab, or on the clipboard.'
  print -u2 'In Firefox, use the Van TV extension button instead.'
  exit 1
}

case ${1:-} in
  -h|--help) usage ;;
  pause|resume|stop) post control "{\"action\":\"$1\"}"; exit ;;
  status) curl -fsS -m 10 "$VAN_TV/api/status"; print; exit ;;
esac
url=${1:-$(front_tab_url)}
is_youtube "$url" || usage
read -r id start <<< "$(video_id "$url")" || { print -u2 "No video id in $url"; exit 1 }
post cast "{\"videoId\":\"$id\",\"position\":$start}" >/dev/null
print -r -- "Casting to Van TV: $url"
