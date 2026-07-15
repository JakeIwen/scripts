#!/usr/bin/env python3
# Live lsusb watcher. Present devices are green; devices that vanish turn
# red (kept on screen with unplug time). Newly plugged devices appear green
# with their plug time. Root hubs are white. USB disks show their filesystem
# labels (bigboi, movingparts, ...) in cyan. Works over ssh in any ANSI terminal.
#
# Usage: usb_watch.py [interval_seconds]   (default 1)

import glob
import os
import re
import subprocess
import sys
import time
from datetime import datetime

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
WHITE = "\033[37m"
CYAN = "\033[36m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"
HOME = "\033[H"        # cursor to top-left
CLEAR_ALL = "\033[2J"  # erase entire screen (once at startup, kills the MOTD)
CLEAR_BELOW = "\033[J" # erase from cursor to end of screen
ERASE_EOL = "\033[K"   # erase to end of line (kills residue from longer old lines)
HIDE_CURSOR = "\033[?25l"
SHOW_CURSOR = "\033[?25h"

LINE_RE = re.compile(r"Bus (\d+) Device (\d+): (ID \S+ ?.*)")


def snapshot():
    """Return {(bus, 'ID vvvv:pppp desc'): [devnum, ...]} currently present.

    Device numbers change on every replug so they're not part of the key,
    but they're kept as values for the sysfs filesystem-label lookup.
    """
    out = subprocess.run(["lsusb"], capture_output=True, text=True, check=True).stdout
    devs = {}
    for line in out.splitlines():
        m = LINE_RE.match(line)
        if m:
            devs.setdefault((m.group(1), m.group(3).strip()), []).append(int(m.group(2)))
    return devs


def usb_labels():
    """Return {(bus, devnum): [fs labels]} for USB-attached block devices."""
    part_labels = {}
    bylabel = "/dev/disk/by-label"
    if os.path.isdir(bylabel):
        for lbl in os.listdir(bylabel):
            dev = os.path.basename(os.path.realpath(os.path.join(bylabel, lbl)))
            part_labels.setdefault(dev, []).append(lbl)

    out = {}
    for blk in glob.glob("/sys/block/*"):
        name = os.path.basename(blk)
        labels = part_labels.get(name, [])[:]  # whole-disk label, if any
        for part in glob.glob(os.path.join(blk, name + "*")):
            labels += part_labels.get(os.path.basename(part), [])
        if not labels:
            continue
        # walk up sysfs from the block device until we hit the USB device dir
        # (the one holding busnum/devnum); non-USB disks never hit one
        p = os.path.realpath(os.path.join(blk, "device"))
        while p and p != "/":
            busf, devf = os.path.join(p, "busnum"), os.path.join(p, "devnum")
            if os.path.isfile(busf) and os.path.isfile(devf):
                with open(busf) as b, open(devf) as d:
                    key = ("%03d" % int(b.read()), int(d.read()))
                out.setdefault(key, []).extend(labels)
                break
            p = os.path.dirname(p)
    return out


def labels_for(key, devnums, labelmap):
    bus = key[0]
    names = sorted({l for dn in devnums for l in labelmap.get((bus, dn), [])})
    return ", ".join(names)


def now():
    return datetime.now().strftime("%H:%M:%S")


def main():
    interval = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0

    # key -> state dict; display is sorted by bus with root hubs first
    seen = {}
    started = now()

    print(HIDE_CURSOR + CLEAR_ALL, end="")
    baseline_pass = True
    try:
        while True:
            current = snapshot()
            labelmap = usb_labels()

            for key, devnums in current.items():
                count = len(devnums)
                labels = labels_for(key, devnums, labelmap)
                if key not in seen:
                    event = None if baseline_pass else f"plugged {now()}"
                    seen[key] = {"present": count, "max": count,
                                 "event": event, "labels": labels}
                else:
                    st = seen[key]
                    if st["present"] == 0 and count > 0:
                        st["event"] = f"replugged {now()}"
                    st["present"] = count
                    st["max"] = max(st["max"], count)
                    if labels:
                        st["labels"] = labels  # sticky: kept after unplug

            for key, st in seen.items():
                if key not in current and st["present"] > 0:
                    st["present"] = 0
                    st["event"] = f"UNPLUGGED {now()}"
            baseline_pass = False

            frame = [
                f"{BOLD}usb_watch{RESET} {DIM}started {started}, "
                f"refresh {interval:g}s — Ctrl+C to exit{RESET}",
                "",
            ]

            # sort by bus number, root hub first within each bus
            def sort_key(item):
                (bus, desc), _st = item
                return (bus, 0 if desc.endswith("root hub") else 1, desc.lower())

            for (bus, desc), st in sorted(seen.items(), key=sort_key):
                if st["present"] == 0:
                    color, mark = RED, "✗"
                elif st["present"] < st["max"]:
                    color, mark = YELLOW, "!"
                elif desc.endswith("root hub"):
                    color, mark = WHITE, "•"
                else:
                    color, mark = GREEN, "✓"

                line = f" {color}{mark} Bus {bus}  {desc}"
                if st["labels"]:
                    line += f"  {CYAN}[{st['labels']}]{color}"
                if st["max"] > 1:
                    line += f"  [{st['present']}/{st['max']}]"
                line += RESET
                if st["event"]:
                    line += f"  {DIM}({st['event']}){RESET}"
                frame.append(line)

            frame.append("")
            frame.append(f"{DIM} {GREEN}green{DIM}=present  {RED}red{DIM}=unplugged  "
                         f"{WHITE}white{DIM}=root hub  {CYAN}cyan{DIM}=fs label  "
                         f"{YELLOW}yellow{DIM}=some of multiple identical devices missing{RESET}")

            # home + redraw (erasing each line's tail) + erase leftovers below
            sys.stdout.write(HOME + (ERASE_EOL + "\n").join(frame) + ERASE_EOL + "\n" + CLEAR_BELOW)
            sys.stdout.flush()
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        print(SHOW_CURSOR, end="")
        print()


if __name__ == "__main__":
    main()
