# Raspberry Pi applications

Long-running Pi applications and their source assets live here. Dashboard,
video library, audiobooks and BME280 use allowlisted package releases under
`/home/pi/scripts/python-packages/`, managed only by `pi/deploy_python.py`.
Each service's initial cutover is explicit (`--activate --service <unit>`), with
its flat unit/drop-ins saved once and per-service restart/GC ownership records.
The flat directory is frozen for rollback; neither broad sync nor the retired
video deployer may write it. See [package deployment and rollback](../docs/deployment.md)
for the supervised checklist and exact recovery commands. Keep Rank 3 import
guards; the separately deployed system monitor is not converted here.

- `van_dashboard/` contains the dashboard backend and browser assets served by
  `van-dashboard.service` on port `8788`.
- `video_library/video_library_server.py` provides the Movies & TV manager
  served by `video-library.service` on port `8789`.
  `video_asset_catalog.py` supplies durable work/asset/session history and
  rollback-compatible legacy projection; `video_qbittorrent.py` resolves the
  same payload across incomplete and final paths. The dashboard links to the
  manager using the current LAN or Tailscale host.
