# Van Movies & TV

[Pi documentation index](../../README.md)

`video-library.service` serves a phone-friendly movie and television library on
port `8789`:

```text
http://vanpi.lan:8789/
```

The Van Dashboard links to the same port while preserving the hostname or
Tailscale address used to reach the dashboard. Like the dashboard, this service
has no login layer and is intended only for the trusted van LAN and Tailscale
ACL. Do not forward port `8789` from a public interface.

## What it indexes

The service reads the cleaned symlinks made by `pi/scripts/alias_media.sh` under
`/mnt/movingparts/links`. If that drive is unavailable, it can use the reviewed
backup index under `/mnt/bigboi/mp_backup/links`. It checks the corresponding
mount point before scanning and never mounts a disk itself.

Movies, documentaries, and episodes are assigned stable opaque IDs. Duplicate
items in `New` and their permanent category are folded together. Shows are
grouped and naturally ordered by season and episode. Search clients never send
paths or shell fragments.

Progress is stored in
`~/.local/share/van-video-library/progress.sqlite3`. The first scan imports
matching entries from `~/vlc-positions.txt`, preserving the useful history from
the existing `.bashrc` workflow. Playback resumes with a short rewind, queues
the remaining episodes in a show, and marks items watched near the credits.
The service does not alter the legacy position log. Its cron writer continues
to record shell-launched `/links/` playback, but deliberately ignores the
manager's revalidated real-target launches so it cannot add entries that the
legacy `resume()` function would misinterpret.

## Player behavior

Playback uses the existing Pi GUI and MPRIS session bus. VLC is launched as a
transient user service, so restarting the web manager does not stop a video in
progress. A launch wakes the display, requests the existing `rear_movie` Sonos
optical setup, selects English audio and the preferred non-forced English
subtitle track, and uses `v4l2-request` hardware decoding. VLC output is pinned
at 100%; listening level is controlled only on the physical rear Sonos stereo
zone. The resolver requires both known rear speakers and exactly one visible
stereo master, so it cannot accidentally change the front/party group.
The slider stays unavailable while `rear_movie` is still preparing, because
that setup applies its calibrated starting volume of 47%. Subtitles can be
disabled for the next launch.

Every paused-to-playing transition through the manager re-runs `rear_movie`
and waits for it to finish before VLC continues. The current or last-known rear
Sonos volume is then restored, so reasserting the optical topology does not
silently replace a chosen listening level with 47%. The legacy `vlcmd
PlayPause` and `pp` helpers used by BetterTouchTool route through this same
control path. If room setup fails or times out, VLC remains paused.

The web player includes the controls represented in the BetterTouchTool Media
strip: previous/next, play/pause, stop, ±20 seconds, and ±5 minutes. It also has
scrubbable progress, rear Sonos volume, VLC playback speed, a sleep timer,
keyboard shortcuts,
per-episode watched state, random unwatched playback, and the existing
favorite-show random/no-subtitle preferences.

Keyboard controls are `Space`/`K` for play-pause, `J`/`L` or the arrow keys for
±20 seconds, Shift with those seek keys for ±5 minutes, `P`/`N` for
previous/next, and `F` for fullscreen when VLC exposes it.

## CLI

The deployed `.bashrc` exposes the same API:

```bash
vid                         # status
vid "30 Rock"               # continue the show
vid -r "The Simpsons"       # random episode
vid -p                      # play/pause
vid -b                      # back 20 seconds
vid -F                      # forward 5 minutes
vid -n                      # next episode
vid -x                      # stop
vid -l "loop"               # fuzzy search
```

## Deployment and checks

From this repository on the Mac:

```bash
cd /Users/jacobr/dev/scripts
media_test_tmp="${TMPDIR:-/tmp}"
media_test_tmp="${media_test_tmp%/}"
media_test_venv="$(mktemp -d "$media_test_tmp/van-video-venv.XXXXXX")"
python3 -m venv "$media_test_venv"
"$media_test_venv/bin/python" -m pip install Flask
PYTHONPATH="$PWD" "$media_test_venv/bin/python" -W error::ResourceWarning \
  -m unittest pi.tests.media.test_video_library_server
./pi/sync_scripts.sh
ssh pi@vanpi.lan 'systemctl --no-pager --full status video-library.service'
ssh pi@vanpi.lan 'curl -fsS http://localhost:8789/api/status | python3 -m json.tool'
```

The temporary test environment can then be removed without touching the
checkout:

```bash
case "$media_test_venv" in
  "$media_test_tmp"/van-video-venv.*) find "$media_test_venv" -depth -delete ;;
  *) echo "refusing unexpected venv path: $media_test_venv" >&2 ;;
esac
```

See [dashboard testing](../dashboard/DASHBOARD_TESTING.md) for the equivalent
isolated-vanpi workflow. Both applications import Flask during their tests.

The generic sync stages the Python entry point and its uniquely named
template/static assets, installs and enables the unit, and restarts it when any
declared runtime asset changes.
