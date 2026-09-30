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
#   stamp-video-thumbs.zsh [options] <file-or-folder> [file-or-folder ...]
#     -r, --recursive    descend into subfolders
#     -f, --force        re-stamp files that already have a custom icon
#         --verify-existing  check icon metadata; re-stamp broken/missing icons
#         --verify-rendered  compare macOS's icon with the stored image; refresh mismatches
#         --refresh-existing reapply stored icons; generate only missing/broken ones
#         --revert       remove custom icons instead of adding them
#     -s, --size N       image pixel size for new/refreshed icons (default 256)
#         --timeout N    seconds per generator/helper operation (default 180)
#     -n, --dry-run      list what would be done, change nothing
#     -h, --help         this text
#
# Existing resource forks are skipped by default. --verify-existing checks the
# Finder flag, resource map, and ICNS header without decoding thumbnail pixels
# or reading video data. Small random reads can still be slow on SMB shares.
# Combine with --dry-run for a read-only scan of the target files.
# If valid stored icons still appear generic in Finder, --refresh-existing
# reapplies those images without rendering thumbnails from the videos again.
# Both new and refreshed images are fitted to --size before stamping. Existing
# icons that are skipped keep their original resolution. Use 512 for more detail.
# Refresh requires the same logged-in GUI session as normal stamping.
# --verify-rendered also requires a GUI session, including with --dry-run. It
# queries macOS's icon service; a Finder window with its own stale display can
# still disagree. Blank/featureless icon-service results are reported as errors.
# A successful match does not confirm that Finder displays the thumbnail.
# Do not use Finder's AppleScript "update" command to refresh these icons:
# it has cleared FinderInfo and resource-fork icon data on this SMB setup.
# FLV content bypasses QuickLook, even with an .mp4 name. FFmpeg is also used
# when QuickLook fails; it is optional (PATH or the local Performance Audio app).

set -u
setopt extendedglob

SELF=${0:A}
EXTS='mp4|m4v|mov|avi|mkv|webm|mpg|mpeg|wmv|flv|ts|m2ts'
CACHE="$HOME/.cache/stamp-video-thumbs"
SIZE=256
TIMEOUT=180
RECURSIVE=0 FORCE=0 REVERT=0 DRYRUN=0 VERIFY_EXISTING=0 REFRESH_EXISTING=0 VERIFY_RENDERED=0
folders=()

usage() { sed -n '2,/^$/p' "$SELF" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while (( $# )); do
  case "$1" in
    -r|--recursive) RECURSIVE=1 ;;
    -f|--force)     FORCE=1 ;;
    --verify-existing) VERIFY_EXISTING=1 ;;
    --verify-rendered) VERIFY_RENDERED=1 ;;
    --refresh-existing) REFRESH_EXISTING=1 ;;
    --revert)       REVERT=1 ;;
    -s|--size)      (( $# >= 2 )) || { print -u2 -- "--size needs a pixel count"; exit 2; }; shift; SIZE="$1" ;;
    --timeout)      (( $# >= 2 )) || { print -u2 -- "--timeout needs seconds"; exit 2; }; shift; TIMEOUT="$1" ;;
    -n|--dry-run)   DRYRUN=1 ;;
    -h|--help)      usage ;;
    -*)             print -u2 "unknown option: $1"; usage 1 ;;
    *)              folders+=("$1") ;;
  esac
  shift
done
(( ${#folders[@]} )) || usage 1
if [[ "$SIZE" != <-> ]] || (( SIZE < 16 || SIZE > 4096 )); then
  print -u2 -- "--size must be an integer from 16 to 4096 (256 or 512 recommended)"; exit 2
fi
if [[ "$TIMEOUT" != <-> ]] || (( TIMEOUT < 1 || TIMEOUT > 3600 )); then
  print -u2 -- "--timeout must be an integer from 1 to 3600"; exit 2
fi

# ---------------------------------------------------------------- swift helper
# stampicon <video> <png> [size]: stamps png as custom icon, verifies by reading the
# icon back and pixel-comparing. exit 0 ok, 1 fail, 3 source png too dark.
# stampicon --check <video>: exit 0 valid structure, 4 needs stamp, 5 I/O error.
# stampicon --check-image <video>: also decode the stored image, without writes.
# stampicon --check-rendered <video>: also compare macOS's icon; exit 6 needs refresh.
# stampicon --refresh <video> [size]: resize/reapply the existing icon image.
build_helper() {
  mkdir -p "$CACHE"
  cat > "$CACHE/stampicon.swift.new" <<'EOF'
import AppKit
import ImageIO
import Darwin
import UniformTypeIdentifiers

// The watchdog owns a separate child process group. Timeouts and Ctrl+C only
// stop that group, never Finder or the shared QuickLook/icon services.
var watchedGroup: pid_t = 0
func stopWatchedGroup(_ signum: Int32) {
    if watchedGroup > 0 { _ = kill(-watchedGroup, SIGKILL) }
    _exit(128 + signum)
}

func runWithTimeout(_ seconds: Double, _ command: [String]) -> Never {
    guard !command.isEmpty, seconds > 0 else { exit(125) }
    var attributes: posix_spawnattr_t?
    guard posix_spawnattr_init(&attributes) == 0 else { exit(125) }
    guard posix_spawnattr_setflags(&attributes, Int16(POSIX_SPAWN_SETPGROUP)) == 0,
          posix_spawnattr_setpgroup(&attributes, 0) == 0 else { exit(125) }
    signal(SIGINT, stopWatchedGroup)
    signal(SIGTERM, stopWatchedGroup)
    let strings = command.map { strdup($0) }
    var argv = strings + [nil]
    let environment = ProcessInfo.processInfo.environment.map { strdup("\($0.key)=\($0.value)") }
    var envp = environment + [nil]
    var pid: pid_t = 0
    let result = posix_spawnp(&pid, strings[0]!, nil, &attributes, &argv, &envp)
    posix_spawnattr_destroy(&attributes)
    for string in strings { free(string) }
    for value in environment { free(value) }
    guard result == 0 else {
        fputs("cannot start \(command[0]): \(String(cString: strerror(result)))\n", stderr)
        exit(125)
    }
    watchedGroup = pid
    let deadline = ProcessInfo.processInfo.systemUptime + seconds
    var status: Int32 = 0
    while true {
        let finished = waitpid(pid, &status, WNOHANG)
        if finished == pid {
            watchedGroup = 0
            let signal = status & 0x7f
            exit(signal == 0 ? ((status >> 8) & 0xff) : 128 + signal)
        }
        if finished < 0 && errno != EINTR { exit(125) }
        if ProcessInfo.processInfo.systemUptime >= deadline {
            _ = kill(-pid, SIGTERM)
            usleep(250_000)
            // Keep the unreaped leader PID reserved until the group is killed.
            _ = kill(-pid, SIGKILL)
            for _ in 0..<20 {
                if waitpid(pid, &status, WNOHANG) == pid { break }
                usleep(50_000)
            }
            watchedGroup = 0
            fputs("timed out after \(Int(seconds))s: \(command[0])\n", stderr)
            exit(124)
        }
        usleep(25_000)
    }
}

func openResourceFork(_ path: String,
                      opener: (String) -> Int32 = { open($0, O_RDONLY | O_CLOEXEC) },
                      pause: () -> Void = { usleep(250_000) }) -> (Int32, Int32) {
    var code: Int32 = 0
    for attempt in 0..<3 {
        let fd = opener(path + "/..namedfork/rsrc")
        if fd >= 0 { return (fd, 0) }
        code = errno
        if ![ENOENT, ESTALE, EAGAIN].contains(code) { break }
        if attempt < 2 { pause() }
    }
    return (-1, code)
}

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
func checkIcon(_ file: String, loadImage: Bool = false) throws -> NSImage? {
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

    let (fd, openError) = openResourceFork(file)
    if fd < 0 {
        let code = openError
        // Confirm the video still exists before treating ENOENT as a missing fork.
        if code == ENOENT {
            var videoStat = stat()
            if stat(file, &videoStat) == 0 {
                let advertised = getxattr(file, "com.apple.ResourceFork", nil, 0, 0, 0)
                let attributeError = errno
                if advertised == 0 || (advertised < 0 && attributeError == ENOATTR) {
                    throw IconCheckFailure.invalid("resource fork missing")
                }
                throw IconCheckFailure.io("resource fork could not be opened after 3 attempts; not treating it as missing")
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
            if !loadImage { return nil }
            guard length <= 64 * 1_048_576 else {
                throw IconCheckFailure.io("icon too large to refresh safely")
            }
            let bytes = Data(try read(dataOffset + offset + 4, length))
            guard let source = CGImageSourceCreateWithData(bytes as CFData, nil),
                  let cg = CGImageSourceCreateImageAtIndex(source, 0,
                    [kCGImageSourceShouldCacheImmediately: true] as CFDictionary),
                  CGImageSourceGetStatusAtIndex(source, 0) == .statusComplete else {
                throw IconCheckFailure.invalid("stored icon image cannot be decoded")
            }
            // ImageIO may return a blank placeholder even for malformed ICNS data.
            guard let pixels = CGContext(data: nil, width: 16, height: 16,
                bitsPerComponent: 8, bytesPerRow: 64, space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGBitmapInfo.byteOrder32Big.rawValue | CGImageAlphaInfo.premultipliedLast.rawValue),
                  let buffer = pixels.data else {
                throw IconCheckFailure.io("cannot inspect stored icon pixels")
            }
            pixels.draw(cg, in: CGRect(x: 0, y: 0, width: 16, height: 16))
            let rgba = buffer.bindMemory(to: UInt8.self, capacity: 1024)
            guard (0..<256).contains(where: { rgba[$0 * 4 + 3] > 0 }) else {
                throw IconCheckFailure.invalid("stored icon image cannot be decoded (blank image)")
            }
            return NSImage(cgImage: cg, size: .zero)
        }
    }
    throw IconCheckFailure.invalid("custom icon missing from resource map")
}

let renderedIconDifferenceLimit = 0.10

// Normalize visible pixels against white; keep alpha separate long enough to
// distinguish an unavailable icon service from a real generic document icon.
func iconSamples(_ image: NSImage) throws -> [Double] {
    let side = 32
    var rect = NSRect(x: 0, y: 0, width: side, height: side)
    guard let cg = image.cgImage(forProposedRect: &rect, context: nil, hints: nil),
          let ctx = CGContext(data: nil, width: side, height: side, bitsPerComponent: 8,
            bytesPerRow: side * 4, space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGBitmapInfo.byteOrder32Big.rawValue | CGImageAlphaInfo.premultipliedLast.rawValue),
          let buffer = ctx.data else {
        throw IconCheckFailure.io("cannot render icon for comparison")
    }
    ctx.interpolationQuality = .medium
    let scale = min(CGFloat(side) / CGFloat(cg.width), CGFloat(side) / CGFloat(cg.height))
    let w = CGFloat(cg.width) * scale, h = CGFloat(cg.height) * scale
    ctx.draw(cg, in: CGRect(x: (CGFloat(side) - w) / 2, y: (CGFloat(side) - h) / 2, width: w, height: h))
    let rgba = buffer.bindMemory(to: UInt8.self, capacity: side * side * 4)
    let alpha = (0..<(side * side)).reduce(0.0) { $0 + Double(rgba[$1 * 4 + 3]) / 255 }
    guard alpha > Double(side * side) * 0.01 else {
        throw IconCheckFailure.io("macOS returned a blank/unavailable icon; run from a logged-in GUI Terminal (not SSH or a sandbox)")
    }
    // Ignore outer padding/shadows, as in the stamping readback check.
    var samples = [Double]()
    for y in 4..<28 {
        for x in 4..<28 {
            let i = (y * side + x) * 4
            let white = 255 - Double(rgba[i + 3])
            for channel in 0..<3 { samples.append((Double(rgba[i + channel]) + white) / 255) }
        }
    }
    return samples
}

func iconDifference(_ expected: [Double], _ actual: [Double]) -> Double {
    zip(expected, actual).reduce(0.0) { $0 + abs($1.0 - $1.1) } / Double(expected.count)
}

func hasIconDetail(_ samples: [Double]) -> Bool {
    for channel in 0..<3 {
        let values = stride(from: channel, to: samples.count, by: 3).map { samples[$0] }
        if let low = values.min(), let high = values.max(), high - low > 2.0 / 255 { return true }
    }
    return false
}

func systemIconSamples(_ image: NSImage) throws -> [Double] {
    let samples = try iconSamples(image)
    // Without icon-service access AppKit can also return an opaque, uniform
    // gray square. It is not the detailed generic MP4 document icon.
    guard hasIconDetail(samples) else {
        throw IconCheckFailure.io("macOS returned a featureless placeholder; rendered verification is unavailable in this session (run from a logged-in GUI Terminal)")
    }
    return samples
}

func needsIconRefresh(_ expected: [Double], _ actual: [Double], generic: [Double]? = nil) -> Bool {
    if let generic = generic,
       iconDifference(actual, generic) < 0.02, iconDifference(expected, generic) >= 0.02 {
        return true
    }
    return iconDifference(expected, actual) >= renderedIconDifferenceLimit
}

// Return a reason only when a usable system icon consistently disagrees.
// These reads do not write metadata or regenerate a QuickLook thumbnail.
func renderedMismatch(_ file: String, _ stored: NSImage) throws -> String? {
    let expected = try iconSamples(stored)
    let ext = URL(fileURLWithPath: file).pathExtension
    let type = UTType(filenameExtension: ext) ?? .movie
    let generic = try? systemIconSamples(NSWorkspace.shared.icon(for: type))
    var last: [Double]?
    var difference = 0.0
    var unavailable: Error?
    for attempt in 0..<3 {
        if attempt > 0 { usleep(200_000) }
        do {
            let actual = try systemIconSamples(NSWorkspace.shared.icon(forFile: file))
            difference = iconDifference(expected, actual)
            if !needsIconRefresh(expected, actual, generic: generic) { return nil }
            last = actual
            unavailable = nil
        } catch { unavailable = error; last = nil }
    }
    if let error = unavailable { throw error }
    guard let actual = last else { throw IconCheckFailure.io("macOS icon is unavailable") }
    if let generic = generic,
       iconDifference(actual, generic) < 0.02 {
        return "macOS returned the generic \(ext) icon"
    }
    return "macOS icon differs from stored thumbnail (\(String(format: "%.3f", difference)))"
}

func sizedIcon(_ image: NSImage, _ side: Int) throws -> NSImage {
    guard let source = image.cgImage(forProposedRect: nil, context: nil, hints: nil),
          let ctx = CGContext(data: nil, width: side, height: side, bitsPerComponent: 8,
            bytesPerRow: side * 4, space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGBitmapInfo.byteOrder32Big.rawValue | CGImageAlphaInfo.premultipliedLast.rawValue) else {
        throw IconCheckFailure.io("cannot resize icon image")
    }
    ctx.interpolationQuality = .high
    let scale = min(CGFloat(side) / CGFloat(source.width), CGFloat(side) / CGFloat(source.height))
    let w = CGFloat(source.width) * scale, h = CGFloat(source.height) * scale
    ctx.draw(source, in: CGRect(x: (CGFloat(side) - w) / 2, y: (CGFloat(side) - h) / 2, width: w, height: h))
    guard let resized = ctx.makeImage() else { throw IconCheckFailure.io("cannot create resized icon") }
    return NSImage(cgImage: resized, size: .zero)
}

// Command dispatch
if CommandLine.arguments.count >= 4 && CommandLine.arguments[1] == "--run-timeout" {
    runWithTimeout(Double(CommandLine.arguments[2]) ?? 0, Array(CommandLine.arguments.dropFirst(3)))
}
if CommandLine.arguments.count == 3 && CommandLine.arguments[1] == "--container" {
    let fd = open(CommandLine.arguments[2], O_RDONLY | O_CLOEXEC)
    guard fd >= 0 else { print("cannot open video header: \(String(cString: strerror(errno)))"); exit(5) }
    var bytes = [UInt8](repeating: 0, count: 3)
    let count = read(fd, &bytes, 3)
    close(fd)
    guard count >= 0 else { print("cannot read video header"); exit(5) }
    print(count == 3 && bytes == [0x46, 0x4c, 0x56] ? "flv" : "other")
    exit(0)
}
if CommandLine.arguments.count == 3 && ["--check", "--check-image", "--check-rendered"].contains(CommandLine.arguments[1]) {
    do {
        let mode = CommandLine.arguments[1], file = CommandLine.arguments[2]
        let stored = try checkIcon(file, loadImage: mode != "--check")
        if mode == "--check-rendered", let stored = stored,
           let reason = try renderedMismatch(file, stored) {
            print(reason); exit(6)
        }
        exit(0)
    }
    catch let error as IconCheckFailure {
        print(error)
        switch error { case .invalid: exit(4); case .io: exit(5) }
    } catch { print(error); exit(5) }
}

let refreshing = CommandLine.arguments[1] == "--refresh"
let file = CommandLine.arguments[refreshing ? 2 : 1]
let targetSize = CommandLine.arguments.count > 3 ? (Int(CommandLine.arguments[3]) ?? 0) : 256
guard (16...4096).contains(targetSize) else { print("invalid icon size"); exit(2) }

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

var img: NSImage
if refreshing {
    do {
        guard let stored = try checkIcon(file, loadImage: true) else {
            print("stored icon unavailable"); exit(4)
        }
        img = stored
    } catch let error as IconCheckFailure {
        print(error)
        switch error { case .invalid: exit(4); case .io: exit(5) }
    } catch { print(error); exit(5) }
} else {
    let png = CommandLine.arguments[2]
    guard let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: png) as CFURL, nil),
          let cg = CGImageSourceCreateImageAtIndex(src, 0, nil) else { print("bad png"); exit(1) }
    img = NSImage(cgImage: cg, size: .zero)
}
do { img = try sizedIcon(img, targetSize) }
catch { print(error); exit(1) }
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
            do { _ = try checkIcon(file); exit(0) }
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

# Run each potentially blocking tool in its own supervised process group.
run_bounded() { "$CACHE/stampicon" --run-timeout "$TIMEOUT" "$@"; }

# ---------------------------------------------------------------- gather files
typeset -aU files=()
for d in "${folders[@]}"; do
  if [[ -f "$d" ]]; then
    if [[ "${d:t}" == ._* || "$d" != *.(#i)(${~EXTS}) ]]; then
      print -u2 "not a supported video file: $d"; exit 2
    fi
    files+=("$d"); continue
  fi
  if [[ ! -d "$d" ]]; then print -u2 "not a file or folder: $d"; exit 2; fi
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
if (( VERIFY_RENDERED && ! FORCE && ! REFRESH_EXISTING )); then
  print -- "-- checking macOS's icon service; Finder's window display is not verified"
fi
build_helper
FFMPEG=${commands[ffmpeg]:-}
if [[ -z "$FFMPEG" ]]; then
  bundled_ffmpeg="${SELF:h:h}/build/Performance Audio.app/Contents/Resources/bin/ffmpeg"
  [[ ! -x "$bundled_ffmpeg" ]] || FFMPEG="$bundled_ffmpeg"
fi
TMPD=$(mktemp -d)
trap 'rm -rf "$TMPD"' EXIT

ok=0; skipped=0; failed=0; planned=0; refreshed=0; planned_refresh=0; i=0
failures=()
for f in "${files[@]}"; do
  ((i++))
  base="${f:t}"
  reason=""
  needs_refresh=0
  if (( ! FORCE )); then
    if (( VERIFY_EXISTING || REFRESH_EXISTING || VERIFY_RENDERED )); then
      check_mode=--check
      (( ! VERIFY_RENDERED )) || check_mode=--check-rendered
      (( ! REFRESH_EXISTING )) || check_mode=--check-image
      if reason=$(run_bounded "$CACHE/stampicon" "$check_mode" "$f" 2>&1); then
        if (( REFRESH_EXISTING )); then
          needs_refresh=1
        else
          ((skipped++))
          if (( VERIFY_RENDERED )); then
            print "skip (macOS icon matches stored thumbnail) [$i/$total] $base"
          else
            print "skip (icon metadata valid) [$i/$total] $base"
          fi
          continue
        fi
      else
        check_status=$?
        if (( check_status == 6 && VERIFY_RENDERED )); then
          needs_refresh=1
        elif (( check_status != 4 )); then
          ((failed++)); failures+=("$base (icon check failed: $reason)")
          print "FAIL [$i/$total] $base (icon check failed: $reason)"; continue
        fi
      fi
    elif xattr "$f" 2>/dev/null | grep -q com.apple.ResourceFork; then
      ((skipped++)); print "skip (already stamped) [$i/$total] $base"; continue
    fi
  fi
  if (( needs_refresh )); then
    if (( DRYRUN )); then
      ((planned_refresh++)); print "would refresh stored icon [$i/$total] $base${reason:+ ($reason)}"
    elif err=$(run_bounded "$CACHE/stampicon" --refresh "$f" "$SIZE" 2>&1); then
      ((refreshed++)); print "refreshed stored icon [$i/$total] $base${reason:+ ($reason)}"
    else
      refresh_status=$?
      if (( refresh_status == 4 )); then
        # The stored icon disappeared between the check and refresh. The helper
        # retried opening it and confirmed absence; generate a replacement.
        needs_refresh=0; reason="$err"
      else
        ((failed++)); failures+=("$base (icon refresh failed: $err)")
        print "FAIL [$i/$total] $base (icon refresh failed: $err)"
      fi
    fi
    (( ! needs_refresh )) || continue
  fi
  if (( DRYRUN )); then
    ((planned++)); print "would stamp [$i/$total] $base${reason:+ ($reason)}"; continue
  fi
  [[ -z "$reason" ]] || print "needs stamp [$i/$total] $base ($reason)"

  # unique dir per file: recursive mode can have duplicate basenames
  wd="$TMPD/$i"; mkdir -p "$wd"
  png="$wd/$base.png"
  if ! container=$(run_bounded "$CACHE/stampicon" --container "$f" 2>&1); then
    ((failed++)); failures+=("$base (video header check failed: $container)")
    print "FAIL [$i/$total] $base (video header check failed: $container)"; continue
  fi
  if [[ "$container" != flv ]]; then
    print "generating thumbnail [$i/$total] $base (QuickLook; ${TIMEOUT}s limit)"
    if ! run_bounded qlmanage -t -s "$SIZE" -o "$wd" "$f" >"$wd/generator.log" 2>&1; then
      # A timed-out generator may leave a partial PNG; never stamp that output.
      rm -f "$png"
    fi
  else
    print "FLV content [$i/$total] $base (using FFmpeg)"
  fi
  if [[ ! -s "$png" ]]; then
    if [[ -z "$FFMPEG" ]]; then
      ((failed++)); failures+=("$base (no thumbnail; FFmpeg fallback unavailable — brew install ffmpeg)")
      print "FAIL [$i/$total] $base (no thumbnail; FFmpeg fallback unavailable — brew install ffmpeg)"; continue
    fi
    print "generating thumbnail [$i/$total] $base (FFmpeg; ${TIMEOUT}s limit)"
    if ! run_bounded "$FFMPEG" -hide_banner -loglevel error -nostdin -ss 2 -i "$f" \
        -map 0:v:0 -frames:v 1 -an -sn -dn \
        -vf "scale=$SIZE:$SIZE:force_original_aspect_ratio=decrease:reset_sar=1" \
        -update 1 -y "$png" >"$wd/generator.log" 2>&1 || [[ ! -s "$png" ]]; then
      detail=$(tail -c 1200 "$wd/generator.log")
      ((failed++)); failures+=("$base (FFmpeg thumbnail failed: $detail)")
      print "FAIL [$i/$total] $base (FFmpeg thumbnail failed: $detail)"; continue
    fi
  fi

  print "stamping icon [$i/$total] $base (${TIMEOUT}s limit)"
  if err=$(run_bounded "$CACHE/stampicon" "$f" "$png" "$SIZE" 2>&1); then
    ((ok++)); print "ok [$i/$total] $base"
  else
    stamp_status=$?
    # Retry ordinary stamping failures once without deleting any metadata.
    # Timeouts/read failures do not justify clearing a previously valid icon.
    if (( stamp_status == 1 )) && err=$(run_bounded "$CACHE/stampicon" "$f" "$png" "$SIZE" 2>&1); then
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
  (( ! REFRESH_EXISTING && ! VERIFY_RENDERED )) || print -- "-- $planned_refresh stored icons would refresh"
else
  print -- "-- done: $ok stamped, $skipped skipped, $failed failed (of $total)"
  (( ! REFRESH_EXISTING && ! VERIFY_RENDERED )) || print -- "-- $refreshed stored icons refreshed"
fi
if (( ${#failures[@]} )); then
  print "failures:"
  printf '  %s\n' "${failures[@]}"
  exit 1
fi
