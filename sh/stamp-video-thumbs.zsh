#!/bin/zsh
# stamp-video-thumbs.zsh — stamp QuickLook (qlmanage) thumbnails onto video files
# as Finder custom icons.
#
# Works around the macOS 26 regression where Finder's thumbnail extension grabs
# frame 0 of a video (black for anything that fades in), by rendering each
# file's thumbnail with qlmanage's legacy generator (which picks a smarter
# frame) and stamping that image as the file's custom icon. Custom icons take
# precedence over generated thumbnails and survive QuickLook cache resets.
#
# The icon lives in com.apple.ResourceFork + com.apple.FinderInfo xattrs; on
# SMB volumes that means an `._<name>` AppleDouble sidecar file (~1MB each).
# Fully reversible with --revert.
#
# Requires: Xcode Command Line Tools (swiftc), and a logged-in GUI session
# (NSWorkspace.setIcon does not work over plain SSH).
#
# Usage:
#   stamp-video-thumbs.zsh [options] <folder> [folder ...]
#     -r, --recursive    descend into subfolders
#     -f, --force        re-stamp files that already have a custom icon
#         --revert       remove custom icons instead of adding them
#     -s, --size N       thumbnail pixel size (default 1024)
#     -n, --dry-run      list what would be done, change nothing
#     -h, --help         this text

set -u
setopt extendedglob

SELF=${0:A}
EXTS='mp4|m4v|mov|avi|mkv|webm|mpg|mpeg|wmv|flv|ts|m2ts'
CACHE="$HOME/.cache/stamp-video-thumbs"
SIZE=1024
RECURSIVE=0 FORCE=0 REVERT=0 DRYRUN=0
folders=()

usage() { sed -n '2,25p' "$SELF" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while (( $# )); do
  case "$1" in
    -r|--recursive) RECURSIVE=1 ;;
    -f|--force)     FORCE=1 ;;
    --revert)       REVERT=1 ;;
    -s|--size)      shift; SIZE="$1" ;;
    -n|--dry-run)   DRYRUN=1 ;;
    -h|--help)      usage ;;
    -*)             print -u2 "unknown option: $1"; usage 1 ;;
    *)              folders+=("$1") ;;
  esac
  shift
done
(( ${#folders[@]} )) || usage 1

# ---------------------------------------------------------------- swift helper
# stampicon <video> <png>: stamps png as custom icon, verifies by reading the
# icon back and pixel-comparing. exit 0 ok, 1 fail, 3 source png too dark.
build_helper() {
  mkdir -p "$CACHE"
  cat > "$CACHE/stampicon.swift.new" <<'EOF'
import AppKit
import ImageIO

let file = CommandLine.arguments[1]
let png = CommandLine.arguments[2]

func grid(_ image: NSImage, _ side: Int) -> [Double]? {
    guard let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil),
          let ctx = CGContext(data: nil, width: side, height: side, bitsPerComponent: 8,
                              bytesPerRow: side, space: CGColorSpaceCreateDeviceGray(),
                              bitmapInfo: CGImageAlphaInfo.none.rawValue) else { return nil }
    ctx.interpolationQuality = .medium
    ctx.setFillColor(CGColor(gray: 1, alpha: 1))
    ctx.fill(CGRect(x: 0, y: 0, width: side, height: side))
    let w = CGFloat(cg.width), h = CGFloat(cg.height)
    let scale = min(CGFloat(side)/w, CGFloat(side)/h)
    ctx.draw(cg, in: CGRect(x: (CGFloat(side)-w*scale)/2, y: (CGFloat(side)-h*scale)/2,
                            width: w*scale, height: h*scale))
    guard let data = ctx.data else { return nil }
    let buf = data.bindMemory(to: UInt8.self, capacity: side*side)
    return (0..<side*side).map { Double(buf[$0]) / 255.0 }
}

guard let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: png) as CFURL, nil),
      let cg = CGImageSourceCreateImageAtIndex(src, 0, nil) else { print("bad png"); exit(1) }
let img = NSImage(cgImage: cg, size: .zero)
guard let want = grid(img, 16) else { print("grid fail"); exit(1) }

// refuse to stamp an essentially-black thumbnail — defeats the purpose
if want.reduce(0, +) / Double(want.count) < 0.02 { print("source png is black"); exit(3) }

for attempt in 1...3 {
    _ = NSWorkspace.shared.setIcon(img, forFile: file, options: [])
    usleep(300_000)
    let back = NSWorkspace.shared.icon(forFile: file)
    if let got = grid(back, 16) {
        // compare central rows only; icon letterboxing differs at the edges
        let idx = (4*16)..<(12*16)
        let diff = idx.map { abs(want[$0] - got[$0]) }.reduce(0, +) / Double(idx.count)
        if diff < 0.12 { exit(0) }
        if attempt == 3 { print("verify mismatch \(String(format: "%.3f", diff))") }
    }
}
exit(1)
EOF
  if [[ ! -x "$CACHE/stampicon" ]] || ! cmp -s "$CACHE/stampicon.swift.new" "$CACHE/stampicon.swift"; then
    mv "$CACHE/stampicon.swift.new" "$CACHE/stampicon.swift"
    print -u2 "compiling icon helper..."
    swiftc -O -o "$CACHE/stampicon" "$CACHE/stampicon.swift" || { print -u2 "swiftc failed — Xcode CLT installed?"; exit 2 }
  else
    rm -f "$CACHE/stampicon.swift.new"
  fi
}

# ---------------------------------------------------------------- gather files
files=()
for d in "${folders[@]}"; do
  if [[ ! -d "$d" ]]; then print -u2 "not a folder: $d"; exit 2; fi
  if (( RECURSIVE )); then
    for f in "$d"/**/*.(#i)(${~EXTS})(N.); do
      [[ "${f:t}" == ._* ]] || files+=("$f")
    done
  else
    for f in "$d"/*.(#i)(${~EXTS})(N.); do
      [[ "${f:t}" == ._* ]] || files+=("$f")
    done
  fi
done
total=${#files[@]}
(( total )) || { print "no video files found"; exit 0 }

# --------------------------------------------------------------------- revert
if (( REVERT )); then
  removed=0
  for f in "${files[@]}"; do
    if xattr "$f" 2>/dev/null | grep -q com.apple.ResourceFork; then
      if (( DRYRUN )); then
        print "would revert: ${f:t}"
      else
        xattr -d com.apple.ResourceFork "$f" 2>/dev/null
        xattr -d com.apple.FinderInfo  "$f" 2>/dev/null
        print "reverted: ${f:t}"
      fi
      ((removed++))
    fi
  done
  print -- "-- $removed of $total files had custom icons"
  exit 0
fi

# ---------------------------------------------------------------------- stamp
build_helper
TMPD=$(mktemp -d)
trap 'rm -rf "$TMPD"' EXIT

ok=0; skipped=0; failed=0; i=0
failures=()
for f in "${files[@]}"; do
  ((i++))
  base="${f:t}"
  if (( ! FORCE )) && xattr "$f" 2>/dev/null | grep -q com.apple.ResourceFork; then
    ((skipped++)); print "skip (already stamped) [$i/$total] $base"; continue
  fi
  if (( DRYRUN )); then print "would stamp [$i/$total] $base"; continue; fi

  # unique dir per file: recursive mode can have duplicate basenames
  wd="$TMPD/$i"; mkdir -p "$wd"
  qlmanage -t -s "$SIZE" -o "$wd" "$f" >/dev/null 2>&1
  png="$wd/$base.png"
  if [[ ! -s "$png" ]]; then
    ((failed++)); failures+=("$base (no thumbnail — unsupported format?)")
    print "FAIL [$i/$total] $base (qlmanage produced nothing)"; continue
  fi

  if err=$("$CACHE/stampicon" "$f" "$png" 2>&1); then
    ((ok++)); print "ok [$i/$total] $base"
  else
    # setIcon refuses to overwrite an existing/corrupt resource fork on SMB:
    # strip stale icon state and try once more
    xattr -d com.apple.ResourceFork "$f" 2>/dev/null
    xattr -d com.apple.FinderInfo  "$f" 2>/dev/null
    if err=$("$CACHE/stampicon" "$f" "$png" 2>&1); then
      ((ok++)); print "ok (retry) [$i/$total] $base"
    else
      ((failed++)); failures+=("$base ($err)")
      print "FAIL [$i/$total] $base ($err)"
    fi
  fi
  rm -rf "$wd"
done

print -- "-- done: $ok stamped, $skipped skipped, $failed failed (of $total)"
if (( ${#failures[@]} )); then
  print "failures:"
  printf '  %s\n' "${failures[@]}"
  exit 1
fi
