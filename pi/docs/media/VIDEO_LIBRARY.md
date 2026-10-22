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

## Catalog repair history

The reviewed October 7, 2026 repair handled 2,013 same-file pairs. The October 8
Silo S03E07 identity and legacy device-churn repair recorded 2,132 changes. Both
completed with no-op reruns. Their snapshot-specific utilities and exclusive
tests are retained in Git history; they were never in the package allowlist.
Runtime identity matching and its regression coverage remain maintained.

Audit evidence and pre-repair backups remain on vanpi under
`~/.local/share/van-video-library/`: `repair-audit-20261007/`,
`video-catalog-final-20261007T044424Z.sqlite3` and
`identity-repair-20261008-0249/`. Preserve those artifacts when investigating
historical catalog state; do not replay an old repair against new observations.

## Player behavior

Playback uses the existing Pi GUI and MPRIS session bus. VLC is launched as a
transient user service, so restarting the web manager does not stop a video in
progress. A launch wakes the display, selects English audio and the preferred
non-forced English subtitle track, and uses `v4l2-request` hardware decoding.
VLC output is pinned at 100%; listening level is controlled only on the physical
rear Sonos stereo zone. The resolver requires both known rear speakers and
exactly one visible stereo master, so it cannot accidentally change the
front/party group. Subtitles can be disabled for the next launch.

Launches and paused/stopped-to-playing transitions start VLC without waiting
for Sonos. About 1.5 seconds later a background job runs `sns.sh
ensure_rear_movie`: optical autoplay can undo the group only after audio starts
flowing, so preparing the room before playback is too early. Pending triggers
are coalesced to the latest resume; checks never overlap, and an intervening
pause/stop cancels a pending check. A trigger during a running check retains
one delayed follow-up. Failures are logged to the service journal, never fail
playback, and time out after 45 seconds (the existing
`VAN_VIDEO_ROOM_PREPARE_TIMEOUT` override is retained). Volume controls remain
available during the check.

The check does nothing when vonMid is on TV/optical and playing, the visible
rear stereo master is grouped under vonMid, vonMid is muted, and the rear
master is unmuted. Otherwise it restores the movie arrangement while keeping
the rear master's current volume, including zero. It prints one `ok` or
`repaired` line, listing what was wrong when it repairs. A missing/split rear
pair or unreadable state fails without guessing. `rear_movie` still applies
its manual 47% default; `rear_movie_resume` and the manual BTT button are unchanged.

The legacy `vlcmd Play`, `vlcmd PlayPause`, and `pp` BTT helpers already route
through the manager's control API and therefore get the same delayed check,
without a duplicate shell-side job. Their Van TV routes stay on the receiver's
HTTP API; that receiver owns its checks. Direct legacy `run_vlc` starts instead
background `~/scripts/rear_movie_check.sh` after launching VLC: it sleeps 1.5
seconds, coalesces jobs with a per-user `flock`, bounds the command with
`timeout`, and logs failures with `logger`. `prep_for_media` still stops old
players and wakes the screen, but no longer runs Sonos before playback.

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

From the primary Mac checkout, run the whole offline suite and use the package
release owner (the old `deploy_video_library.sh` now refuses all deployment and
rollback operations):

```bash
cd /Users/jacobr/dev/scripts
.local/test-venv/bin/python run_tests.py
python3 pi/deploy_python.py --dry-run --update --service video-library.service
python3 pi/deploy_python.py --update --service video-library.service
ssh pi@vanpi.lan 'systemctl --no-pager --full status video-library.service'
ssh pi@vanpi.lan 'curl -fsS http://localhost:8789/api/status | python3 -m json.tool'
```

See [deployment](../deployment.md) for activation prerequisites, release records,
scoped update behavior and saved-unit rollback. `--service` selects unit actions,
not release contents: utility code and the shared current link also advance.
Never write to the frozen `/home/pi/scripts/python-automation/` rollback tree.

For the delayed-audio change, compare and back up the live shell files first.
Ship `pi/sns.sh` to `/home/pi/sns.sh` before the package update: its ensure-only
branch avoids ALSA writes and execs Python so a timeout cannot orphan a repair.
After the package update, ship `pi/scripts/rear_movie_check.sh` to
`/home/pi/scripts/rear_movie_check.sh` and apply only the reviewed media-function
changes from `pi/.bashrc` to `/home/pi/.bashrc`, preserving unrelated live edits.
New SSH shells pick up the functions; already-open interactive shells need to
source `~/.bashrc`. Broad `pi/sync_scripts.sh` owns these same shell files but
also ships private inputs and unrelated services; it is not needed for this
scoped update.

After deployment, check a launch and a pause/resume through both the web UI and
BTT: video should continue immediately and the rear speakers should recover
after the delayed check without a volume reset. Repeat while the arrangement
is already correct and check that no regrouping occurs. Inspect failures with
`journalctl -u video-library.service -n 80 --no-pager`; direct legacy starts log
under the `rear-movie` tag. These hardware checks are separate from the offline
fake-SoCo, fake-MPRIS and temporary-shell test suite.
