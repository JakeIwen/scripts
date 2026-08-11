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

The cleaned name is now catalog metadata, not the permanent identity. The
catalog keeps separate random IDs for a logical work, an exact playable asset,
and each playback session. Paths, parser keys, symlink names, and torrent names
are time-versioned aliases. Renaming a cleaned symlink or changing a parsing
rule therefore does not discard the asset's history.

Progress is stored in
`~/.local/share/van-video-library/progress.sqlite3`. The v2 tables are additive
and use a `video_v2_` prefix; the original `progress` and `metadata` tables are
not altered. Each material checkpoint appends a playback event, updates the
asset playhead and separate work-level watched state, and projects the same
result into the original `progress` row in one transaction. The first scan also
imports matching entries from `~/vlc-positions.txt`, preserving the useful
history from the existing `.bashrc` workflow. Playback resumes with a short
rewind, queues the remaining episodes in a show, and marks complete assets
watched near the credits.

The active VLC track is resolved once and pinned to its playback session.
Polling never tries to identify that session again from a changing filename.
For a qBittorrent file, the durable locator is the local client ID, torrent ID,
and file index. That locator stays the same when the payload moves from
`torrent/incomplete` into `New`, `TV`, `Movies`, or another permanent torrent
category; the completion alias job merely records the new path. Incomplete
assets retain their playhead but are never
automatically marked watched from a partial duration.

`playp <file>` remains a universal local-file launcher. It uses the manager's
normal display, Sonos, subtitle, and fixed-VLC-volume setup for any regular
media file. qBittorrent matching is best effort: an unrecognized file receives
a provisional asset and plays immediately, and an unavailable manager falls
back to the original direct `run_vlc` implementation only when it can prove no
connection was made. An ambiguous timeout never launches a second VLC.
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
playp "random local clip.mkv" # tracked when possible; never torrent-only
```

## Deployment and checks

From this isolated development clone on the Mac:

```bash
cd /Users/jacobr/dev/scripts_2
media_test_tmp="${TMPDIR:-/tmp}"
media_test_tmp="${media_test_tmp%/}"
media_test_venv="$(mktemp -d "$media_test_tmp/van-video-venv.XXXXXX")"
python3 -m venv "$media_test_venv"
"$media_test_venv/bin/python" -m pip install Flask
PYTHONPATH="$PWD" "$media_test_venv/bin/python" -W error::ResourceWarning \
  -m unittest \
    pi.tests.media.test_video_asset_catalog \
    pi.tests.media.test_video_qbittorrent \
    pi.tests.media.test_video_identity_integration \
    pi.tests.media.test_video_history_edges \
    pi.tests.media.test_video_identity_regressions \
    pi.tests.media.test_video_identity_deployment \
    pi.tests.media.test_video_library_server
./pi/deploy_video_library.sh
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

Do not run the repository-wide `pi/sync_scripts.sh` from this secondary clone;
it also owns ignored secrets and unrelated services. The scoped deployer stages
only the media service and creates an immutable, checked
`progress.pre-v2.sqlite3` snapshot before first installation.

Rollback does not restore or delete the live database. It reinstalls the exact
pre-v2 server, unit, shell controls, web assets, and Sonos helper from the
pinned baseline; that server ignores all `video_v2_` tables and immediately
reads the continuously maintained legacy rows. The safer alias builder remains
installed because its loopback notification is inert against v1, while the old
copy could delete seeded RAR payloads:

```bash
cd /Users/jacobr/dev/scripts_2
./pi/deploy_video_library.sh --rollback
```

After time on the old server, redeploying v2 imports changed legacy rows and
treats rows explicitly deleted by the old server as clear-history tombstones.
