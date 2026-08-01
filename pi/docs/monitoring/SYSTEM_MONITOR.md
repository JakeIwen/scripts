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

`vanpi-usb-controller-watchdog.timer` separately checks once a minute for the
current-boot kernel message that declares the Pi 4 VL805 xHCI controller dead.
It sends one rate-limited ntfy alert per boot with the guarded recovery command.
It never removes, rescans, resets, or automatically reboots the controller:
those operations can hang the kernel or discard mounted-disk state. Use
`sudo /home/pi/scripts/safe_reboot.sh`; if USB remains absent after the
guarded reboot, shut down and fully power-cycle the Pi and powered hub.

The known RTL9201 storage enclosure is configured to use the kernel's
`usb-storage` driver instead of UAS after its UAS error recovery killed the
whole VL805 controller. Install or verify the device-specific boot quirk with:

```bash
sudo /home/pi/scripts/configure_rtl9201_uas_quirk.sh
```

The helper preserves a one-time pre-change copy of `cmdline.txt`, refuses
multiple non-empty lines or ambiguous existing parameters, and reports whether
a reboot is required. The quirk is only for USB VID:PID `0bda:9201`.

## RTL9201/VL805 failure signature

On 2026-07-31 the daily EXFAT snapshot cleanly unmounted and spun down
`hdd1tb` in its RTL9201 enclosure. A later disk-health pass used a broad
`blkid -t LABEL=EXFAT512` lookup; that lookup touched the unrelated sleeping
RTL9201 disk. UAS commands timed out, device resets escalated to an xHCI host
system error, and the kernel declared the VL805 controller dead, disconnecting
both USB buses. No firmware undervoltage or USB over-current event accompanied
the failure.

`disk_health_watchdog.sh` now verifies only the exact source already mounted
at `/mnt/EXFAT512`, so its minutely check cannot probe unrelated sleeping
disks. The RTL9201-specific IGNORE_UAS boot quirk limits any future error
recovery on that enclosure to the simpler `usb-storage` driver. A guarded
reboot restored the dead controller; PCIe remove/rescan remains prohibited
because it has deadlocked this host before.

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
