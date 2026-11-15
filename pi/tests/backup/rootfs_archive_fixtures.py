"""Fake archive volume for rootfs_tidy_archive.py tests: findmnt and mountinfo.

The fake findmnt prints the way util-linux does for the flags it is given:
`-r` joins columns with one space; without it columns are padded to their
header width (`/mnt/EXFAT512 exfat  122C-A219`, the format that broke the shell
comparison). CLI for the shell test: `python3 rootfs_archive_fixtures.py
BIN_DIR MOUNT [UUID [FSTYPE]]` writes BIN_DIR/findmnt and BIN_DIR/mountinfo.
"""
import json
import os
import sys

UUID = "122C-A219"
FSTYPE = "exfat"

FAKE_FINDMNT = r'''#!{python}
import json, sys
volume = json.loads({volume!r})
args = sys.argv[1:]
columns = args[args.index("-o") + 1].split(",") if "-o" in args else ["TARGET"]
if "--mountpoint" not in args or args[args.index("--mountpoint") + 1] != volume["TARGET"]:
    sys.exit(1)
values = [volume[name] for name in columns]
if any(flag.startswith("-") and not flag.startswith("--") and "r" in flag for flag in args):
    print(" ".join(value.replace(" ", "\\x20") for value in values))
else:
    print(" ".join(value.ljust(len(name)) for name, value in zip(columns, values)).rstrip())
'''


def device_text(path):
    device = os.stat(path).st_dev
    return f"{os.major(device)}:{os.minor(device)}"


def write_volume(bin_dir, mount, uuid=UUID, fstype=FSTYPE, device=None):
    """Write a fake findmnt and mountinfo describing `mount`; return both paths."""
    os.makedirs(bin_dir, exist_ok=True)
    device = device or device_text(mount)
    volume = {"TARGET": mount, "MAJ:MIN": device, "FSTYPE": fstype, "UUID": uuid}
    findmnt = os.path.join(bin_dir, "findmnt")
    with open(findmnt, "w", encoding="utf-8") as handle:
        handle.write(FAKE_FINDMNT.format(python=sys.executable, volume=json.dumps(volume)))
    os.chmod(findmnt, 0o755)
    mountinfo = os.path.join(bin_dir, "mountinfo")
    escaped = mount.replace("\\", "\\134").replace(" ", "\\040")
    with open(mountinfo, "w", encoding="utf-8") as handle:
        handle.write("22 1 179:2 / / rw,noatime shared:1 - ext4 /dev/root rw\n")
        handle.write(f"404 26 {device} / {escaped} rw,relatime shared:340"
                     f" - {fstype} /dev/sda rw,uid=1000,gid=1000,iocharset=utf8\n")
    return findmnt, mountinfo


if __name__ == "__main__":
    write_volume(*sys.argv[1:])
