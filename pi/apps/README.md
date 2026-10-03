# Raspberry Pi applications

Long-running Pi applications and their source assets live here. The dashboard
uses allowlisted package releases under `/home/pi/scripts/python-packages/`.
Initial cutover is explicit; the preserved flat directory remains a rollback
source. Other apps retain their dedicated/legacy deployment contracts. See
[package deployment and rollback](../docs/deployment.md) before deploying.

- `van_dashboard/` contains the dashboard backend and browser assets served by
  `van-dashboard.service` on port `8788`.
- `video_library/video_library_server.py` provides the Movies & TV manager
  served by `video-library.service` on port `8789`.
  `video_asset_catalog.py` supplies durable work/asset/session history and
  rollback-compatible legacy projection; `video_qbittorrent.py` resolves the
  same payload across incomplete and final paths. The dashboard links to the
  manager using the current LAN or Tailscale host.
