# SMART disk alerts

[Pi documentation index](../../README.md)

`vanpi-smart-watch.timer` runs `pi/scripts/smart_watch.sh` as root every 10
minutes. For each label in `HDD_LABELS` (`pi/scripts/disk_policy.sh`) that is
attached and mounted, it takes the disk's shared backup lock, reads SMART once
with `smartctl -j -n standby -d sat -H -A`, and compares the result with the
last run. A disk that is unmounted, in standby, or being unmounted is skipped.

## Alerts

Each alert goes to the `NTFY_WARNING_URL` topic at high priority, once per
change:

| Signal | Alerts when |
| --- | --- |
| SMART health verdict | it changes to failed |
| Reallocated sectors (attribute 5) | the raw count rises; the first reading is a silent baseline |
| Pending sectors (197) | the count is above zero and differs from the last run |
| Offline-uncorrectable sectors (198) | the count is above zero and differs from the last run |
| Temperature | it reaches 60C; it re-arms once the disk cools below 55C |

## Reading log

Every successful reading also appends one line to
`/var/lib/vanpi-smart-watch/history/<label>` (world-readable; the dashboard's
Disks & Torrents sheet graphs it):

```text
<epoch> <passed> <reallocated> <pending> <uncorrectable> <temp C> <power-on hours> <power cycles>
```

A value the drive does not report is `unknown`. Lines older than 30 days are
trimmed once the oldest is a day past that. A gap in the log means the disk was
unmounted or busy being unmounted: parked disks are never read.

## Alert state

State lives in `/var/lib/vanpi-smart-watch/<label>` as one line:
`passed reallocated pending uncorrectable hot`. A failed delivery leaves the
state unchanged, so the next run sends the alert again. Delete a label's file to
reset its baseline.

## Why not smartd

smartmontools is installed, but `smartd` stays disabled. The ignition policy
parks the HDDs while driving, and smartd polls every listed disk on a timer. On
2026-10-30 a spun-down `bigboi` (WD Elements bridge) woke on smartd's
registration and again on a bare `smartctl -n standby` power query: its first
read afterwards took 0.1 to 0.3 s instead of the 4.8 s spin-up. `hdd1tb`'s
JMicron JMS578 bridge cannot answer CHECK POWER MODE at all, so `-n standby` is
ignored there. Reading only mounted disks guarantees a parked disk is never
woken. The cost is coverage: a disk is checked only while it is mounted, so
`bigboi` is read during its backup window and `hdd1tb` only if a run lands
inside its short nightly snapshot. Flash drives such as `EXFAT512` expose no
SMART.

## Check it

```sh
systemctl list-timers vanpi-smart-watch.timer
journalctl -u vanpi-smart-watch.service -n 20
sudo cat /var/lib/vanpi-smart-watch/*
```
