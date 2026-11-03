#!/bin/zsh
# Save a clipboard image through the mounted Pi share without changing the clipboard.
set -euo pipefail

usage() {
  print 'Usage: shimg [-f | -c] [filename]'
  print 'Save the clipboard image to the Pi agent-shared folder as a lowercase-hyphen name.'
  print '  (default), -c, --classify, -n, --name'
  print '      drop it as screenshot-pending-N.png and rename it in the background'
  print '      from a cheap model glance (Haiku or Luna, picked by the usage lean)'
  print '  -f, --fast  name it screenshot-N.png, no model'
  print '  filename    use this name, cleaned the same way; replaces a file of that name'
  print 'An unmounted pihome share is mounted and its folder shown in Finder.'
}

typeset -a help fast classify
zparseopts -D -E -F -- h=help -help=help f=fast -fast=fast \
    c=classify -classify=classify n=classify -name=classify 2>/dev/null ||
  { usage >&2; exit 64; }
if (( ${#help} )); then
  usage
  exit 0
fi
if (( $# > 1 || ${#fast} && ${#classify} || $# && (${#fast} || ${#classify}) )); then
  usage >&2
  exit 64
fi

share=/Volumes/pihome/Documents/agent-shared
share_url=smb://pi@VANPI._smb._tcp.local/pihome
budget_report=$HOME/.agent/usage-budget/report.txt
glance_timeout_s=20
glance_prompt='Name this image file: reply with only 2 or 3 lowercase words joined by hyphens saying what it shows, nothing else.'

# Lowercase letters, digits and single hyphens only: just-simple-name.
clean_name() {
  setopt local_options extended_glob
  local name=${(L)1}
  name=${${${name//[^a-z0-9]##/-}#-}%-}
  print -r -- ${name%-png}
}

# Move $1 to the first free $2-N.png from N=$3 (0: bare $2.png first); never replaces a
# file. Each name is claimed by an O_EXCL create (noclobber) before the move, since
# `mv -n` checks and renames separately and two saves at once could both win.
move_to_free() {
  local src=$1 stem=$2 n=$3 name
  while :; do
    if (( n )); then name=$stem-$n.png; else name=$stem.png; n=1; fi
    if ( setopt no_clobber; : > "$share/$name" ) 2>/dev/null; then
      if /bin/mv -f -- "$src" "$share/$name"; then
        print -r -- "$share/$name"
        return 0
      fi
      /bin/rm -f -- "$share/$name"
      return 1
    fi
    [[ -e $share/$name ]] || return 1
    (( ++n ))
  done
}

# Worker order follows the delegation lean: Haiku answers soonest and costs ~1k
# tokens, so it goes first unless the lean wants GPT spent (or Claude spared).
glance_workers() {
  local lean=''
  if [[ -r $budget_report ]]; then
    lean=${${(M)${(f)"$(<$budget_report)"}:#lean: *}#lean: }
  fi
  case ${lean%% *} in
    conserve-claude) print luna ;;
    gpt-heavy) print luna haiku ;;
    *) print haiku luna ;;
  esac
}

# Claude's own prompt, tools, MCP servers, hooks and CLAUDE.md are left out (user
# settings unloaded); --bare would also drop the subscription's OAuth login.
glance_haiku() {
  printf '{"type":"user","message":{"role":"user","content":[{"type":"image","source":{"type":"base64","media_type":"image/jpeg","data":"%s"}},{"type":"text","text":"%s"}]}}\n' \
    "$(/usr/bin/base64 -i "$1")" "$glance_prompt" > "$2/haiku-in.jsonl"
  ( cd "$2" && /usr/bin/perl -e 'alarm shift; exec @ARGV' $glance_timeout_s \
      claude -p --model haiku --setting-sources project --strict-mcp-config --tools '' \
      --no-session-persistence --disable-slash-commands --system-prompt 'You name image files.' \
      --input-format stream-json --output-format stream-json --verbose \
      < haiku-in.jsonl > haiku-out.jsonl 2>/dev/null ) || return 1
  local last=${${(f)"$(<$2/haiku-out.jsonl)"}[-1]}
  [[ $last == *'"is_error":false'* && $last =~ '"result":"([^"]*)"' ]] || return 1
  print -r -- $match[1]
}

# No user config: skips Codex's MCP servers and hooks, which halves its start-up.
glance_luna() {
  /usr/bin/perl -e 'alarm shift; exec @ARGV' $glance_timeout_s \
    codex exec -m gpt-6-luna -c model_reasoning_effort='"low"' --ignore-user-config \
    --ephemeral --skip-git-repo-check -s read-only --ignore-rules --color never \
    -C "$2" -o "$2/luna-out.txt" -i "$1" -- "$glance_prompt" </dev/null >/dev/null 2>&1 &&
    [[ -r $2/luna-out.txt ]] || return 1
  print -r -- "$(<$2/luna-out.txt)"
}

# Background half: a rough name, only enough to tell this image from the last one.
rename_from_glance() {
  set +eu
  local pending=$1 dir worker slug=''
  dir=$(/usr/bin/mktemp -d -t shimg) || return
  trap '/bin/rm -rf -- "$dir"; exit 1' TERM INT HUP
  if /usr/bin/sips -Z 512 -s format jpeg "$pending" --out "$dir/glance.jpg" >/dev/null 2>&1; then
    for worker in $(glance_workers); do
      slug=$(glance_$worker "$dir/glance.jpg" "$dir") || continue
      slug=$(clean_name "$slug")
      slug=${${slug[1,40]}%-}
      [[ -n $slug ]] && break
    done
  fi
  /bin/rm -rf -- "$dir"
  if [[ -n $slug ]]; then
    move_to_free "$pending" "$slug" 0 >/dev/null
  else
    move_to_free "$pending" screenshot 1 >/dev/null
  fi
}

filename=''
if (( $# )); then
  filename=$(clean_name "$1")
  if [[ -z $filename ]]; then
    usage >&2
    exit 64
  fi
  filename+=.png
fi
if [[ ! -d $share ]]; then
  # Mount quietly, then show the folder; open focuses a Finder window already on it.
  /usr/bin/osascript -e "mount volume \"$share_url\"" >/dev/null 2>&1 || true
  if [[ ! -d $share ]]; then
    print -u2 "Pi share unavailable: $share. Mounting pihome failed (van LAN only)."
    exit 1
  fi
  /usr/bin/open "$share"
fi
if [[ -n $filename && -d $share/$filename ]]; then
  print -u2 "Destination is a directory: $share/$filename"
  exit 1
fi

# Stage beside the destination so failed clipboard reads never truncate an existing image.
staging=$(/usr/bin/mktemp -d "$share/.shimg-XXXXXX")
trap '/bin/rm -rf -- "$staging"' EXIT
# ImageIO logs conversion noise ("Error creating a JP2 color space") on stderr even
# when the read works, so stderr shows only when it fails.
if ! /usr/bin/osascript - "$staging/image.png" 2>"$staging/read.err" <<'APPLESCRIPT'; then
on run argv
  set imageData to the clipboard as «class PNGf»
  set output to open for access (POSIX file (item 1 of argv)) with write permission
  try
    write imageData to output
    close access output
  on error messageText number errorNumber
    close access output
    error messageText number errorNumber
  end try
end run
APPLESCRIPT
  if [[ $(<$staging/read.err) == *'(-1700)'* ]]; then
    print -u2 'No image on the clipboard.'
  else
    /usr/bin/grep -v '^\*\*\* ' "$staging/read.err" >&2 || true
  fi
  exit 1
fi
if [[ -n $filename ]]; then
  /bin/mv -f -- "$staging/image.png" "$share/$filename"
  print -r -- "$share/$filename"
elif (( ${#fast} )); then
  move_to_free "$staging/image.png" screenshot 1
else
  pending=$(move_to_free "$staging/image.png" screenshot-pending 1)
  print -r -- $pending
  rename_from_glance "$pending" </dev/null >/dev/null 2>&1 &!
fi
