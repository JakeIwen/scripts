# Raspberry Pi applications

Long-running Pi applications and their source assets live here. The Mac-side
`pi/sync_scripts.sh` deployment script stages their Python entry points into
vanpi's existing `/home/pi/scripts/python-automation/` directory so deployed
service paths remain stable.

- `van_dashboard/` contains the dashboard backend and browser assets served by
  `van-dashboard.service` on port `8788`.
- `video_library/video_library_server.py` provides the Movies & TV manager
  served by `video-library.service` on port `8789`. The dashboard links to it
  using the current LAN or Tailscale host.
