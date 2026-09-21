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
#         --verify-existing  check icon metadata; re-stamp broken/missing icons
#         --revert       remove custom icons instead of adding them
#     -s, --size N       thumbnail pixel size (default 1024)
#     -n, --dry-run      list what would be done, change nothing
#     -h, --help         this text
#
# Existing resource forks are skipped by default. --verify-existing checks the
# Finder flag, resource map, and ICNS header without decoding thumbnail pixels
# or reading video data. Small random reads can still be slow on SMB shares.
# Combine with --dry-run for a read-only scan of the target files.

set -u
setopt extendedglob

SELF=${0:A}
EXTS='mp4|m4v|mov|avi|mkv|webm|mpg|mpeg|wmv|flv|ts|m2ts'
CACHE="$HOME/.cache/stamp-video-thumbs"
SIZE=1024
RECURSIVE=0 FORCE=0 REVERT=0 DRYRUN=0 VERIFY_EXISTING=0
folders=()

usage() { sed -n '2,/^$/p' "$SELF" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while (( $# )); do
  case "$1" in
    -r|--recursive) RECURSIVE=1 ;;
    -f|--force)     FORCE=1 ;;
    --verify-existing) VERIFY_EXISTING=1 ;;
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
# stampicon --check <video>: exit 0 valid structure, 4 needs stamp, 5 I/O error.
build_helper() {
  mkdir -p "$CACHE"
  cat > "$CACHE/stampicon.swift.new" <<'EOF'
import AppKit
import ImageIO
import Darwin

enum IconCheckFailure: Error, CustomStringConvertible {
    case invalid(String), io(String)
    var description: String {
        switch self { case .invalid(let s), .io(let s): return s }
    }
}

func u16(_ b: [UInt8], _ i: Int) -> Int {
    (Int(b[i]) << 8) | Int(b[i+1])
}
func u32(_ b: [UInt8], _ i: Int) -> Int {
    (u16(b, i) << 16) | u16(b, i+2)
}

// Inspect the on-disk resource directory, not NSWorkspace's cached icon.
// A typical stamped icon needs only 16 + 50 + 12 bytes from its resource fork.
func checkIcon(_ file: String) throws {
    var info = [UInt8](repeating: 0, count: 32)
    let infoSize = getxattr(file, "com.apple.FinderInfo", &info, info.count, 0, 0)
    if infoSize < 0 {
        let code = errno
        if code == ENOATTR { throw IconCheckFailure.invalid("no Finder icon metadata") }
        throw IconCheckFailure.io("reading FinderInfo: \(String(cString: strerror(code)))")
    }
    guard infoSize == 32, u16(info, 8) & 0x0400 != 0 else {
        throw IconCheckFailure.invalid("custom-icon flag missing")
    }

    let fd = open(file + "/..namedfork/rsrc", O_RDONLY | O_CLOEXEC)
    if fd < 0 {
        let code = errno
        // Confirm the video still exists before treating ENOENT as a missing fork.
        if code == ENOENT {
            var videoStat = stat()
            if stat(file, &videoStat) == 0 {
                throw IconCheckFailure.invalid("resource fork missing")
            }
        }
        throw IconCheckFailure.io("opening resource fork: \(String(cString: strerror(code)))")
    }
    defer { close(fd) }
    var st = stat()
    guard fstat(fd, &st) == 0 else {
        throw IconCheckFailure.io("reading resource size: \(String(cString: strerror(errno)))")
    }
    let size = Int(st.st_size)
    func read(_ offset: Int, _ count: Int) throws -> [UInt8] {
        guard offset >= 0, count >= 0, offset <= size, count <= size - offset else {
            throw IconCheckFailure.invalid("truncated resource fork")
        }
        var bytes = [UInt8](repeating: 0, count: count)
        var done = 0
        while done < count {
            let n = bytes.withUnsafeMutableBytes {
                pread(fd, $0.baseAddress!.advanced(by: done), count - done, off_t(offset + done))
            }
            if n < 0 {
                if errno == EINTR { continue }
                throw IconCheckFailure.io("reading resource fork: \(String(cString: strerror(errno)))")
            }
            guard n > 0 else { throw IconCheckFailure.io("resource fork changed during scan") }
            done += n
        }
        return bytes
    }

    let header = try read(0, 16)
    let dataOffset = u32(header, 0), mapOffset = u32(header, 4)
    let dataSize = u32(header, 8), mapSize = u32(header, 12)
    guard dataSize > 0 else { throw IconCheckFailure.invalid("empty resource data/map") }
    guard dataOffset >= 16, mapOffset >= 16, mapSize >= 30,
          dataOffset + dataSize <= size, mapOffset + mapSize <= size,
          dataOffset + dataSize <= mapOffset || mapOffset + mapSize <= dataOffset else {
        throw IconCheckFailure.invalid("invalid resource header")
    }
    guard mapSize <= 1_048_576 else {
        throw IconCheckFailure.io("resource map too large for bounded validation")
    }
    let map = try read(mapOffset, mapSize)
    let types = u16(map, 24)
    guard types >= 28, types + 2 <= map.count else {
        throw IconCheckFailure.invalid("invalid resource type list")
    }
    let typeCount = (u16(map, types) + 1) & 0xffff
    guard types + 2 + typeCount * 8 <= map.count else {
        throw IconCheckFailure.invalid("truncated resource type list")
    }
    for i in 0..<typeCount {
        let entry = types + 2 + i * 8
        if u32(map, entry) != 0x69636e73 { continue } // 'icns'
        let count = (u16(map, entry + 4) + 1) & 0xffff
        let refs = types + u16(map, entry + 6)
        guard refs >= types + 2 + typeCount * 8, refs + count * 12 <= map.count else {
            throw IconCheckFailure.invalid("invalid icon reference list")
        }
        for j in 0..<count {
            let ref = refs + j * 12
            if u16(map, ref) != 0xbfb9 { continue } // custom icon ID -16455
            let offset = u32(map, ref + 4) & 0x00ffffff
            guard offset + 12 <= dataSize else {
                throw IconCheckFailure.invalid("icon reference outside resource data")
            }
            let icon = try read(dataOffset + offset, 12)
            let length = u32(icon, 0)
            guard length >= 16, offset + 4 + length <= dataSize,
                  u32(icon, 4) == 0x69636e73, u32(icon, 8) == length else {
                throw IconCheckFailure.invalid("invalid ICNS header/length")
            }
            return
        }
    }
    throw IconCheckFailure.invalid("custom icon missing from resource map")
}

if CommandLine.arguments.count == 3 && CommandLine.arguments[1] == "--check" {
    do { try checkIcon(CommandLine.arguments[2]); exit(0) }
    catch let error as IconCheckFailure {
        print(error)
        switch error { case .invalid: exit(4); case .io: exit(5) }
    } catch { print(error); exit(5) }
}

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
    guard NSWorkspace.shared.setIcon(img, forFile: file, options: []) else {
        if attempt == 3 { print("setIcon failed") }
        continue
    }
    usleep(300_000)
    let back = NSWorkspace.shared.icon(forFile: file)
    if let got = grid(back, 16) {
        // compare central rows only; icon letterboxing differs at the edges
        let idx = (4*16)..<(12*16)
        let diff = idx.map { abs(want[$0] - got[$0]) }.reduce(0, +) / Double(idx.count)
        if diff < 0.12 {
            do { try checkIcon(file); exit(0) }
            catch {
                if attempt == 3 { print("stored icon verification failed: \(error)") }
                continue
            }
        }
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

ok=0; skipped=0; failed=0; planned=0; i=0
failures=()
for f in "${files[@]}"; do
  ((i++))
  base="${f:t}"
  reason=""
  if (( ! FORCE )); then
    if (( VERIFY_EXISTING )); then
      if reason=$("$CACHE/stampicon" --check "$f" 2>&1); then
        ((skipped++)); print "skip (icon metadata valid) [$i/$total] $base"; continue
      else
        check_status=$?
        if (( check_status != 4 )); then
          ((failed++)); failures+=("$base (icon check failed: $reason)")
          print "FAIL [$i/$total] $base (icon check failed: $reason)"; continue
        fi
      fi
    elif xattr "$f" 2>/dev/null | grep -q com.apple.ResourceFork; then
      ((skipped++)); print "skip (already stamped) [$i/$total] $base"; continue
    fi
  fi
  if (( DRYRUN )); then
    ((planned++)); print "would stamp [$i/$total] $base${reason:+ ($reason)}"; continue
  fi
  [[ -z "$reason" ]] || print "needs stamp [$i/$total] $base ($reason)"

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

if (( DRYRUN )); then
  print -- "-- dry run: $planned would stamp, $skipped skipped, $failed failed (of $total)"
else
  print -- "-- done: $ok stamped, $skipped skipped, $failed failed (of $total)"
fi
if (( ${#failures[@]} )); then
  print "failures:"
  printf '  %s\n' "${failures[@]}"
  exit 1
fi
