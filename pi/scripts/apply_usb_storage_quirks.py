#!/usr/bin/python3 -I
"""Install the Seagate IGNORE_UAS boot quirk; never reboot or touch USB devices."""

import argparse
import difflib
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone


CMDLINE = Path("/boot/firmware/cmdline.txt")
QUIRK_ID = "0bc2:2344"
PREFIXES = ("usb-storage.quirks=", "usb_storage.quirks=")


def tokens(data):
    """Retain byte positions and whitespace; refuse ambiguous kernel syntax."""
    if not data.endswith(b"\n") or data.count(b"\n") != 1:
        raise ValueError("cmdline must be exactly one line with one final newline")
    text = data[:-1].decode("ascii")
    if not text.strip(" \t") or any(ord(c) < 32 and c != "\t" for c in text):
        raise ValueError("cmdline is empty or contains control characters")
    if "\x7f" in text or text.count('"') % 2:
        raise ValueError("cmdline contains DEL or unmatched quotes")
    words = list(re.finditer(r'(?:[^ \t"]|"[^"]*")+', text))
    if any(word.group() == "--" for word in words):
        raise ValueError("refusing a cmdline with an init-argument separator (--)")
    return text, words


def quirk_parameter(words):
    matches = [word for word in words if word.group().startswith(PREFIXES)]
    # Quoted parameter names are legal to the kernel but deliberately unsupported.
    if any(word.group().replace('"', '').startswith(PREFIXES) and '"' in word.group()
           for word in words):
        raise ValueError("quoted quirk parameters are unsupported")
    if any(word.group() in (prefix[:-1] for prefix in PREFIXES) for word in words):
        raise ValueError("quirk parameter is missing its equals sign")
    if len(matches) > 1:
        raise ValueError("multiple usb-storage.quirks parameters (including '_' alias)")
    return matches[0] if matches else None


def merge_quirks(value):
    entries = value.split(",") if value else []
    seen = set()
    found = False
    for index, entry in enumerate(entries):
        if not re.fullmatch(r"[0-9a-fA-F]{4}:[0-9a-fA-F]{4}:[a-z]*", entry):
            raise ValueError("invalid VID:PID:flags quirk entry")
        identity, flags = entry.rsplit(":", 1)
        normalized = identity.lower()
        if normalized in seen:
            raise ValueError("duplicate quirk device identity")
        seen.add(normalized)
        if normalized == QUIRK_ID:
            found = True
            if "u" not in flags:
                entries[index] = entry + "u"
    if not found:
        entries.append(QUIRK_ID + ":u")
    merged = ",".join(entries)
    # usb-storage's module_param_string buffer is 128 bytes, including NUL.
    if len(merged) > 127:
        raise ValueError("quirk value would exceed the kernel's 127-byte limit")
    return merged


def add_quirk(original):
    text, words = tokens(original)
    parameter = quirk_parameter(words)
    if parameter is None:
        # Insert before trailing whitespace, leaving every original byte in place.
        start = end = words[-1].end()
        replacement = " " + PREFIXES[0] + QUIRK_ID + ":u"
    else:
        start, end = parameter.span()
        name, value = parameter.group().split("=", 1)
        replacement = name + "=" + merge_quirks(value)
    result = (text[:start] + replacement + text[end:] + "\n").encode("ascii")
    _, new_words = tokens(result)
    new_parameter = quirk_parameter(new_words)
    old_other = [w.group() for w in words if w is not parameter]
    new_other = [w.group() for w in new_words if w is not new_parameter]
    if (old_other != new_other or result[:start] != original[:start]
            or result[start + len(replacement):] != original[end:]):
        raise ValueError("edit changed bytes outside the quirk parameter")
    # ARM64's command-line buffer includes a terminating NUL.
    if len(result) > 2048:
        raise ValueError("cmdline would exceed the 2048-byte ARM64 buffer")
    return result


def read_regular(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError(f"not a regular, singly linked file: {path}")
        data = stream.read(8193)
        if len(data) > 8192:
            raise ValueError(f"file is too large: {path}")
    return data, metadata


def same_file(left, right):
    return all(getattr(left, name) == getattr(right, name) for name in (
        "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns",
        "st_uid", "st_gid", "st_mode", "st_nlink"))


def write_synced(fd, data, original_metadata):
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        actual = os.fstat(stream.fileno())
        # FAT ownership/mode come from mount options; avoid unnecessary chown/chmod.
        if (actual.st_uid, actual.st_gid) != (original_metadata.st_uid, original_metadata.st_gid):
            os.fchown(stream.fileno(), original_metadata.st_uid, original_metadata.st_gid)
        mode = stat.S_IMODE(original_metadata.st_mode)
        if stat.S_IMODE(actual.st_mode) != mode:
            os.fchmod(stream.fileno(), mode)
        actual = os.fstat(stream.fileno())
        if ((actual.st_uid, actual.st_gid, stat.S_IMODE(actual.st_mode)) !=
                (original_metadata.st_uid, original_metadata.st_gid, mode)):
            raise ValueError("could not preserve cmdline ownership and mode")
        os.fsync(stream.fileno())


def install(path, original, result, metadata, directory_fd):
    # Refuse unsupported directory fsync before creating even the backup.
    os.fsync(directory_fd)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup = path.with_name(path.name + ".bak-" + stamp)
    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    write_synced(fd, original, metadata)
    os.fsync(directory_fd)
    if read_regular(backup)[0] != original:
        raise ValueError("backup read-back verification failed")
    print(f"Backup: {backup}", flush=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        write_synced(fd, result, metadata)
        if read_regular(temporary)[0] != result:
            raise ValueError("staged file read-back verification failed")
        current, current_metadata = read_regular(path)
        if current != original or not same_file(metadata, current_metadata):
            raise ValueError("cmdline changed concurrently; refusing replacement")
        os.replace(temporary, path)
        os.fsync(directory_fd)
        installed, installed_metadata = read_regular(path)
        tokens(installed)
        if installed != result:
            raise ValueError("installed file read-back verification failed")
        if ((installed_metadata.st_uid, installed_metadata.st_gid, installed_metadata.st_mode) !=
                (metadata.st_uid, metadata.st_gid, metadata.st_mode)):
            raise ValueError("installed file metadata verification failed")
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)
    print("Verified boot cmdline update. Running kernel unchanged; no reboot performed.")


def verify_runtime():
    """Read-only, parked/normal-mount verification; no device probes or repairs."""
    running = Path("/proc/cmdline").read_bytes()
    _, words = tokens(running)
    parameter = quirk_parameter(words)
    if parameter is None:
        raise ValueError("running kernel cmdline has no quirk parameter")
    value = parameter.group().split("=", 1)[1]
    if merge_quirks(value) != value:
        raise ValueError("running kernel cmdline lacks Seagate IGNORE_UAS")
    print("/proc/cmdline: " + parameter.group())
    value = Path("/sys/module/usb_storage/parameters/quirks").read_text().strip()
    if merge_quirks(value) != value:
        raise ValueError("usb_storage's live parameter lacks Seagate IGNORE_UAS")
    print("usb_storage live quirks: " + value)
    devices = set()
    for label in ("mbp2tbkup", "movingparts", "EXFAT512"):
        source = (Path("/dev/disk/by-label") / label).resolve(strict=True)
        if label != "EXFAT512":
            block = (Path("/sys/class/block") / source.name).resolve(strict=True)
            parents = list(block.parents)
            usb = next((p for p in parents if (p / "idVendor").exists()), None)
            interface = next((p for p in parents if (p / "bInterfaceClass").exists()), None)
            if usb is None or interface is None:
                raise ValueError(f"{label}: cannot establish USB ancestry")
            if usb in devices:
                raise ValueError("the Seagate labels do not identify two distinct USB devices")
            devices.add(usb)
            identity = ":".join((usb / field).read_text().strip().lower()
                                for field in ("idVendor", "idProduct"))
            driver = (interface / "driver").resolve(strict=True).name
            if identity != QUIRK_ID or driver != "usb-storage":
                raise ValueError(f"{label}: unexpected USB identity/driver {identity} {driver}")
            print(f"{label}: {source}, {identity}, driver={driver}")
        target = "/mnt/" + label
        output = subprocess.check_output(
            ["/usr/bin/findmnt", "--json", "--mountpoint", target,
             "--output", "SOURCE,TARGET,FSTYPE,OPTIONS"], text=True, timeout=10)
        rows = json.loads(output).get("filesystems", [])
        if len(rows) != 1:
            raise ValueError(f"{label}: missing or ambiguous managed mount")
        row = rows[0]
        if (row.get("target") != target or "[" in row.get("source", "")
                or Path(row.get("source", "")).resolve(strict=True) != source
                or "rw" not in row.get("options", "").split(",")
                or "ro" in row.get("options", "").split(",")):
            raise ValueError(f"{label}: mount is not the expected read/write filesystem")
        print(f"{target}: verified source={source}, type={row.get('fstype')}, rw")
    print("PASS: both Seagates use usb-storage; all three automatic managed mounts are rw.")
    print("This checks binding and mount state, not filesystem integrity or sustained I/O.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true",
                        help="read-only post-reboot checks; expects all automatic mounts active")
    parser.add_argument("--dry-run", action="store_true", help="diff only; no writes or backup")
    parser.add_argument("--cmdline", type=Path, default=CMDLINE,
                        help="alternate cmdline for offline media or local tests")
    parser.add_argument("--revert", type=Path, metavar="BACKUP",
                        help="restore only if current cmdline equals this backup plus the quirk")
    args = parser.parse_args(argv)
    if args.verify:
        if args.dry_run or args.revert or args.cmdline != CMDLINE:
            parser.error("--verify cannot be combined with edit options")
        return verify_runtime()
    path = args.cmdline.absolute()
    path = path.parent.resolve(strict=True) / path.name
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if not args.dry_run:
            fcntl.flock(directory_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        original, metadata = read_regular(path)
        if path == CMDLINE and metadata.st_uid != 0:
            raise ValueError("live cmdline must be owned by root")
        if args.revert:
            result, _ = read_regular(args.revert)
            if add_quirk(result) != original:
                raise ValueError("backup plus quirk does not equal current cmdline; manual review required")
        else:
            result = add_quirk(original)
        tokens(original)
        tokens(result)
        if result == original:
            print("No change needed; cmdline already matches. No backup or writes.")
            return 0
        if args.dry_run:
            sys.stdout.writelines(difflib.unified_diff(
                original.decode("ascii").splitlines(keepends=True),
                result.decode("ascii").splitlines(keepends=True),
                fromfile=str(path) + " (before)", tofile=str(path) + " (after)"))
            print("Dry run only; no files changed and no reboot performed.")
            return 0
        if path == CMDLINE:
            if os.geteuid() != 0:
                raise ValueError("run with sudo to update the live cmdline")
            filesystem = subprocess.check_output(
                ["/usr/bin/findmnt", "-rn", "-M", str(path.parent), "-o", "FSTYPE"], text=True)
            if filesystem.strip() != "vfat":
                raise ValueError("/boot/firmware must be a mounted vfat filesystem")
        install(path, original, result, metadata, directory_fd)
        return 0
    finally:
        os.close(directory_fd)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"ERROR: {error}. Do not reboot; inspect cmdline and any printed backup.", file=sys.stderr)
        sys.exit(1)
