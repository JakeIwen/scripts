# Raspberry Pi tooling

This directory contains vanpi applications, services, deployment helpers,
backup and disk tooling, and their operational documentation. Repository paths
in these documents are relative to the repository root; deployed scripts
normally live under `/home/pi/scripts/`.

## Critical operations

- [Python package deployment and rollback](docs/deployment.md)

- [Recovery playbook](docs/backup/RESTORE.md)
- [Time Machine backup](docs/backup/TIME_MACHINE.md)
- [Weekly Pi recovery copies in iCloud](docs/backup/ICLOUD.md)
- [Encrypted offsite USB drives](docs/backup/OFFSITE.md)
- [OpenWrt backup and restore](scripts/backup/OPENWRT_BACKUP.md)
- [UBNT backup and restore](scripts/backup/UBNT_BACKUP.md)
- [Storage and torrent policy](docs/policy/POLICY.md)
- [Lifecycle policy TODO](docs/policy/TODO.md)
- [System event monitor](docs/monitoring/SYSTEM_MONITOR.md)
- [SMART disk alerts](docs/monitoring/SMART.md)

## Apps

- [Van dashboard](https://github.com/JakeIwen/van-dashboard) (not yet published;
  checkout `~/dev/van-dashboard`): the port 8788 dashboard, COP ALERT, hosted
  projects and its own deploy
- [Movies & TV library](docs/media/VIDEO_LIBRARY.md)
- [Deal Watch](docs/price_check/PRICE_CHECK.md)
- [Application layout](apps/README.md)

## Project guides

- [Visual Guides & A/C IR controller](https://github.com/JakeIwen/visual-guides)
  — standalone source in `~/dev/visual-guides`; [Pi](http://vanpi:8791/)
  and [Mac](http://127.0.0.1:8792/) pages.
- [Phone Drop](projects/phone-drop/README.md) — iPhone share-sheet uploads to
  the shared agent folder on port 8793.
- Van TV — YouTube cast receiver, DIAL on port 8794 and status/TV-code pairing
  page on LAN port 8795; standalone source in `~/dev/castit/youtube/`.

## Testing

- [Test suite layout and commands](tests/README.md)

## Networking

- [OpenWrt remote logging](docs/networking/OPENWRT_LOGGING.md)
- [Network flight recorder](docs/networking/NETWORK_FLIGHT_RECORDER.md)
- [Network recorder verification](docs/networking/NETWORK_FLIGHT_RECORDER_VERIFICATION.md)
- [Dashcam Wi-Fi connection research](projects/dashcam-wifi-connect/README.md)

## Compute

- [Van compute broker](../../van-compute/pi/docs/compute/VAN_COMPUTE.md) — standalone
  sibling checkout at `/Users/jacobr/dev/van-compute`.
