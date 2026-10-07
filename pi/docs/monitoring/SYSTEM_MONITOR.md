# Vanpi system event monitor

[Pi documentation index](../../README.md)

`system-event-monitor.service` passively records evidence needed to distinguish
Pi input-power problems from USB hub, cable, enclosure, and device problems.
It does not reset USB devices or change power, clocks, services, or CAN state.

## Recorded evidence

- All Raspberry Pi firmware throttle flags (`vcgencmd get_throttled`), including
  active and sticky since-boot flags for undervoltage, Arm frequency capping,
  throttling, and the soft temperature limit.
- Timestamped kernel undervoltage/recovery, USB connection/error/reset/
  disconnect, USB over-current, storage I/O, OOM, watchdog, hung-task, panic,
  and kernel-warning events.
- Five-second resource observations durably retained as a 48-hour flight
  recorder and summarized into one-minute peak rollups: total CPU, memory,
  swap, load, every readable Linux thermal zone, root usage, Arm clock,
  physical-interface network receive/transmit rates, whole-disk read/write
  rates, IOPS, and disk busy time. Detailed samples also retain Linux
  CPU/memory/I/O pressure, selected VM counters, and passive DRM/framebuffer
  state. Rollups retain the top process, interface, or disk at each peak.
- Live CPU-frequency policies (current/minimum/maximum clock, related cores,
  and governor) are shown alongside the firmware throttle word. The dashboard
  separates flags active now, sticky flags seen since boot, and start/clear
  transitions in the selected time range.
- A live state snapshot on new events: resource state, top processes, USB sysfs
  topology, mounted local filesystems, firmware flags, and failed systemd units.

Process command-line arguments are deliberately not stored because they may
contain credentials or private URLs; peak-process evidence contains only the
kernel process name, PID, CPU percentage, and RSS.

The dashboard groups the CPU and memory leader from every one-minute rollup by
kernel process name. This makes repeat peak leaders visible across restarts
without collecting command-line arguments. It also shows the five current CPU
and memory leaders. A leader count means that the process was busiest at a
sampled peak; it does not by itself prove that the process is faulty.

On the current Raspberry Pi 4 kernel, `cpu-thermal` is one shared CPU/SoC sensor
covering cores 0-3. Linux does not expose an independent temperature for each
core, so the UI labels the sensor and its shared scope rather than duplicating
one value four times. If additional thermal zones appear later, the collector
and dashboard report each zone separately.

At startup the daemon imports relevant messages from the current boot. Those
older events are labeled as journal backfill because a full state snapshot was
not available at their original event time. New events get live snapshots.
Resource rollups are retained for 90 days; noteworthy events are retained. The
48-hour detailed sample ring is bounded by insertion order rather than wall
clock, because boot-time NTP correction can move a Pi's clock. It is written
with SQLite's FULL synchronous mode so a committed sample is recoverable after
abrupt power loss.

The database is `/var/lib/vanpi-monitor/events.sqlite3`. The dashboard accesses
it through bounded monitor commands instead of opening the database itself.
Ordinary health reports are read-only. A user-requested crash analysis writes
only its redacted result back to the monitor database.

Reports aggregate event counts and latest occurrences in SQLite using the
covering `events_report_idx` index. Only the displayed events and undervoltage
transitions are loaded into Python; USB correlations use indexed time windows.
Counts and diagnoses still cover the full selected range, even during a USB
error storm. Existing databases receive the index when the collector initializes;
read-only report commands never migrate the schema.

The dashboard shares report/history results between clients for 10 seconds and
serializes generation. Failed reads have a two-second retry cooldown rather than
serving an old report as fresh. Saving a crash analysis invalidates these caches.
The command timeout remains 15 seconds. Raw events and their retention are unchanged.

`vanpi-usb-controller-watchdog.timer` separately checks once a minute for the
current-boot kernel message that declares the Pi 4 VL805 xHCI controller dead.
It sends one rate-limited ntfy alert per boot with the guarded recovery command.
It never removes, rescans, resets, or automatically reboots the controller:
those operations can hang the kernel or discard mounted-disk state. Use
`sudo /home/pi/scripts/safe_reboot.sh`; if USB remains absent after the
guarded reboot, shut down and fully power-cycle the Pi and powered hub.

## Seagate/VL805 failure and IGNORE_UAS mitigation

On **2026-10-07 at 05:58:51 MDT (11:58:51 UTC)**, UAS write commands to
`mbp2tbkup`, a Seagate Portable 2.5-inch HDD (`0bc2:2344`), timed out.
Device resets escalated to an xHCI Host System Error and `HC died`; both
USB buses disconnected. A guarded reboot restored the controller. This repeats
the July failure signature below with different hardware; it does not prove
that the disk, cable, hub, or power supply is healthy.

The owner approved this active mitigation for **both** Seagate Portables,
`mbp2tbkup` and `movingparts`, which share `0bc2:2344`:

```text
usb-storage.quirks=0bc2:2344:u
```

`u` is IGNORE_UAS: bind that VID:PID to `usb-storage` instead of `uas` at boot.
It is not serial-specific. A modest throughput/queue-depth cost is accepted
for these spinning disks; gigabit Ethernet or 2.4 GHz Wi-Fi usually limits
network transfers first. This avoids one failure path, not all USB failures.

**Preparation status, 2026-10-07:** the utility passed a live `--dry-run`;
no boot file was changed and no reboot was performed for this change. The live
249-byte, one-line cmdline had no quirk token, and the live module parameter was
empty. Do not call the mitigation deployed until post-reboot verification passes.
On kernel `6.12.62+rpt-rpi-v8`, `usb_storage` is built in (absent from `lsmod`,
but `/sys/module/usb_storage/parameters/quirks` exists). An `/etc/modprobe.d`
option therefore will not fix this host; edit the firmware's kernel command
line at `/boot/firmware/cmdline.txt`.

### Apply, verify, and revert

The standalone tool is `pi/scripts/apply_usb_storage_quirks.py` in this repo.
Copy only that file to a private staging directory on the Pi; do not run broad
`sync_scripts.sh` from a worktree. In a Pi shell, set `q` to its absolute staged
path (for example `/tmp/seagate-uas.Jb3n7Y/apply.py` for the prepared run), then:

```bash
python3 -I "$q" --dry-run
sudo python3 -I "$q"
```

The default target must be a root-owned, regular, singly linked file on the
mounted FAT boot filesystem. The tool requires exactly one nonempty ASCII line
with one final LF; CRLF, extra/embedded newlines, NUL/control bytes, ambiguous
quirk syntax, duplicate parameters/devices, and `--` init-argument separators
fail closed. Other parameters, ordering, quotes, and whitespace remain byte-for-
byte unchanged. Existing comma-separated `VID:PID:flags` entries are retained;
the tool appends this device or adds `u` to its existing flags. It recognizes
`usb_storage.quirks=` as the kernel's equivalent spelling and preserves it.
Already-present `u` is a no-op, without another backup. A quirk value exceeding
127 bytes or a resulting ARM64 cmdline exceeding 2048 bytes is refused.

Each actual edit first saves and fsyncs an exclusive UTC backup next to the
original, `cmdline.txt.bak-YYYYMMDDTHHMMSS.microsecondsZ`. Record the exact
`Backup:` path printed by apply. The new file is staged **in the same directory**,
metadata-preserved, fsynced, checked, renamed, directory-fsynced, and read back.
Directory locking serializes this tool's writers; a final source-content and
metadata comparison refuses concurrent edits. It cannot lock out unrelated
editors: do not edit boot settings or run a clone/restore at the same time.
No fsync failure is ignored, including on FAT. If an error occurs after rename,
the new file may already be installed: inspect it and the backup, and **do not
reboot** merely because a backup was printed. Atomic rename/fsync reduces but
cannot eliminate power-loss/corrupt-media risk on FAT.

The utility never reboots, mounts, repairs, unbinds, or resets a device. After a
successful apply, arrange a parked maintenance window with no active writes,
then separately invoke the existing guarded reboot (never plain `reboot`):

```bash
sudo /home/pi/scripts/safe_reboot.sh
```

Reconnect, set `q` again if needed, and verify without changing any mount:

```bash
python3 -I "$q" --verify
cat /proc/cmdline
cat /sys/module/usb_storage/parameters/quirks
lsusb -t
```

`--verify` requires the running kernel cmdline and live module parameter to
contain `0bc2:2344` with `u`. For each named Seagate label it resolves the current
block/sysfs ancestry, checks VID:PID and the USB interface's `usb-storage` driver
(not `uas`), and checks exact mount source plus `rw` at `/mnt/mbp2tbkup`,
`/mnt/movingparts`, and `/mnt/EXFAT512`. It does not assume stable `sdX` names,
probe sleeping disks with a broad `blkid`, or attempt recovery. This is mount
state verification, **not** a filesystem-integrity test or sustained-I/O test.
The two HDD mounts can legitimately be absent under ignition/policy/eject holds;
verify in the normal parked, disks-enabled state rather than forcing mounts.
`bigboi` and `hdd1tb` are manual/backup mounts and are not required to be mounted.
If `/tmp` is cleared on reboot, re-copy the identical tool before verification.

To revert, set `backup` to the exact pre-apply backup path, then:

```bash
sudo python3 -I "$q" --revert "$backup" --dry-run
sudo python3 -I "$q" --revert "$backup"
sudo /home/pi/scripts/safe_reboot.sh
```

Revert uses the same backup/atomic-write/read-back path and refuses unless the
current cmdline equals that saved backup **plus only this quirk edit**. It will
not clobber later kernel-parameter changes or restore a pre-clone root PARTUUID.
After reboot, compare the cmdline/live parameter with the saved original and
check bindings using `lsusb -t`; `--verify` deliberately fails when UAS is restored.
Retain the backup and an independent recovery card until normal workload is stable.

### Boot/backup compatibility audit

- `backup/clone_to_sd.sh` calls external `rpi-clone` (`-U`, or `-f -U` for
  initialization); it does not parse/rewrite cmdline itself. The upstream
  geerlingguy **2.0.27** implementation copies mounted boot files with rsync,
  then substitutes the source disk ID/device reference in the destination
  cmdline, not the whole line. The colon-delimited quirk survives both full
  and incremental clones. This was source-reviewed, **not** a spare clone test
  or inspection of the live external executable; `backup_setup.sh` installs an
  unpinned upstream checkout. Recheck deployed `rpi-clone` before relying on it.
- `backup/pi_backup.sh`, `backup/clone_now.sh`, and `backup/new_hotspare.sh`
  delegate to that clone flow. The new-card tool checks approved SD-reader IDs,
  labels and size, not HDD transport-driver names. No parsing changes are needed.
  `backup_conf.sh` retains staggered 7-/14-day generations; the quirk reaches
  each spare on its **next successful clone**, not immediately. Do not refresh
  both recovery generations just to propagate it.
- Borg creation explicitly includes `/` **and** `/boot/firmware`, even with
  `--one-file-system`; `BORG_EXCLUDES` does not omit cmdline or its backups.
  `backup/icloud_backup.py` transports Borg repository data without interpreting
  boot parameters. Media/exFAT/Time Machine/router backups do not write the Pi's
  boot cmdline. Archives predating this change necessarily lack the mitigation.
- `restore_from_borg.sh` extracts the archive's boot tree and runs
  `s/PARTUUID=[0-9a-f]{8}-0([12])/PARTUUID=$diskid-0\1/g` on fstab/cmdline.
  That substitution matches the identifier only, **not** the rest of the line;
  it preserves this quirk. An old archive restores the old quirk state. Restored
  `.bak-*` files retain their historical root IDs; the guarded revert refuses
  such a backup after the restored root ID changes. The iCloud restore notes
  likewise require replacement-card identifier repair, not a new cmdline.
- `van_dashboard_hotspares.py` reads normalized block topology, clone stamps,
  and label-verified read-only ext4 usage evidence, not boot parameters. The
  dashboard's “current/bootable” evidence and `backup_watchdog.sh`'s stamps or
  `CLONE_INFO.txt` are **not proof** of the quirk's presence on a spare. Do not
  mount a spare to render dashboard evidence.
- `pi/configs/vanpi-crash-evidence.txt` supplies a `ramoops` overlay through
  firmware `config.txt`, not cmdline. Repository sync/deployment has no managed
  `cmdline.txt` to overwrite this edit. `/proc/<pid>/cmdline` readers inspect
  process arguments and are unrelated to the kernel's `/proc/cmdline`.
- The legacy `pi/raspbian_setup.sh` notes invoke external `raspi-config` and
  `rpi-update`; they do not implement a cmdline rewrite. Fresh OS images replace
  boot configuration, and external configuration/kernel tools are not pinned by
  this repo. Recheck the token and actual binding after using them or upgrading
  the kernel; do not assume a restored/reimaged host inherits the quirk.

No in-repo token-parsing incompatibility was found; no clone/restore behavior
or scheduling was changed. `039dbd7` introduced the old RTL9201-only tool and
`350a0a4` removed it after replacement; the new tool does not revive its obsolete
VID:PID or rewrite other quirks. Test locally with:

```bash
bash pi/tests/storage/test_apply_usb_storage_quirks.sh
```

## Retired RTL9201/VL805 failure signature

On 2026-07-31 the daily EXFAT snapshot cleanly unmounted and spun down
`hdd1tb` in its RTL9201 enclosure. A later disk-health pass used a broad
`blkid -t LABEL=EXFAT512` lookup; that lookup touched the unrelated sleeping
RTL9201 disk. UAS commands timed out, device resets escalated to an xHCI host
system error, and the kernel declared the VL805 controller dead, disconnecting
both USB buses. No firmware undervoltage or USB over-current event accompanied
the failure.

`disk_health_watchdog.sh` now verifies only the exact source already mounted
at `/mnt/EXFAT512`, so its minutely check cannot probe unrelated sleeping
disks. An RTL9201-specific IGNORE_UAS boot quirk subsequently limited error
recovery on that enclosure to the simpler `usb-storage` driver. The enclosure
was replaced with a JMS578 unit in August 2026 and the obsolete quirk was
removed. A guarded reboot restored the dead controller; PCIe remove/rescan
remains prohibited because it has deadlocked this host before.

Mount, unmount, dashboard repair, clone discovery, and backup-health checks
also avoid token-only `blkid` searches. They discover exact candidates from
udev's `/dev/disk/by-label` or `/dev/disk/by-partlabel` links, cross-check live
udev database properties for duplicate labels, and use `blkid` only against the
selected device. This keeps deliberately stopped disks out of unrelated label
lookups while preserving fail-closed identity checks.

Network totals include physical Ethernet/Wi-Fi interfaces only so virtual
Tailscale traffic is not counted twice; the dashboard still shows Tailscale as
a separate interface. Disk totals use whole block devices and do not add their
partitions again. Filesystem labels are shown when available.

## Analysis commands

Run a human-readable 24-hour diagnosis:

```bash
/home/pi/scripts/system_event_monitor.py report
```

Choose a range or get the same JSON used by the dashboard:

```bash
/home/pi/scripts/system_event_monitor.py report --hours 168
/home/pi/scripts/system_event_monitor.py report --hours 24 --json
```

Filter normalized events and optionally include the captured state:

```bash
/home/pi/scripts/system_event_monitor.py events --hours 24 --category usb
/home/pi/scripts/system_event_monitor.py events --hours 24 --severity critical --state --json
```

`vanpi-crash-capture.service` runs once on every boot, after persistent journal
and pstore processing but before the continuous monitor. It automatically saves
the preceding boot's redacted journal analysis, up to the final 30 minutes of
detailed flight-recorder samples, and a current-boot hardware/state snapshot.
An atomic JSON copy is also kept under
`/var/lib/vanpi-monitor/crash-reports/`, keyed by boot ID.

Analyze the journal retained from the preceding boot and save/update the report
manually:

```bash
/home/pi/scripts/system_event_monitor.py crash-report --save
/home/pi/scripts/system_event_monitor.py crash-report --save --json
```

Saved analyses are keyed by the preceding boot ID. Running the command again
for the same boot updates that report instead of creating a duplicate. Complete
redacted reports—including relevant kernel/PID-1 timeline records—are retained
for later review, while the dashboard shows a comparison against the most
recent different boot. List the saved history with:

```bash
/home/pi/scripts/system_event_monitor.py crash-history
/home/pi/scripts/system_event_monitor.py crash-history --full --json
```

Crash analysis is read-only with respect to the operating system: it reads the
previous-boot journal and kernel pstore but does not reset devices, restart
services, or alter power state. Log URLs and common token/password assignments
are redacted before a report is returned or stored.

The deployed journald drop-in makes storage explicitly persistent, syncs it at
least every 15 seconds, and keeps up to 300 MiB while reserving 2 GiB of free
root space. `/boot/firmware/config.txt` includes
`vanpi-crash-evidence.txt`, which enables a 256 KiB `ramoops` region. After the
next reboot, a kernel panic/oops or captured console tail can survive in pstore;
`systemd-pstore` archives it under `/var/lib/systemd/pstore/` for the boot hook.

Firmware history bits are sticky until reboot. For a controlled A/B power test,
safely unmount affected storage before disconnecting anything, reboot to clear
the sticky history, establish a hub-disconnected baseline, then add the powered
hub and downstream devices in deliberate stages. A USB error alone implicates a
data path/device more directly; an undervoltage event means the Pi's own input
voltage fell and keeps the PSU, power cable, connectors, upstream wiring, hub
behavior, and aggregate load in scope.
