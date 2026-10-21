# Van TV

YouTube Cast receiver for the Pi's HDMI screen. The phone controls playback,
volume, seeking and the queue. Streams go directly from yt-dlp to mpv, without
loading YouTube's advertising player. SponsorBlock skips `sponsor` and
`selfpromo` segments; unavailable SponsorBlock never prevents playback.

## Deploy

From the scripts checkout on the Mac, while on the van LAN:

```sh
bash pi/projects/youtube-cast/deploy.sh prepare
bash pi/projects/youtube-cast/deploy.sh install
bash pi/projects/youtube-cast/deploy.sh status
```

This project is **not** part of broad scripts sync. Its deployer exclusively
owns `/home/pi/youtube-cast/` and the root-owned regular unit
`/etc/systemd/system/youtube-cast.service`. It installs npm dependencies with
`npm ci --omit=dev` in a new release, creates/upgrades the shared `venv` using
`yt-dlp[default]` and Deno (yt-dlp's JS runtime), switches `current`, and checks supervisor and HTTP health.
No application package is installed as root. Older releases are retained;
`previous` points to the last healthy release. A failed deployment prints the
old release path but does not silently roll back the shared yt-dlp environment.

Prerequisites: Node 20+, npm, mpv, Python 3 with venv/pip, `xset`, `dbus-send`,
curl, an active `pi` X session on `:0`, and `/home/pi/sns.sh`. The installer
checks prerequisites rather than changing the machine's apt packages.
If needed, install OS dependencies in a normal Pi terminal:

```sh
sudo apt-get install mpv python3-venv python3-pip x11-xserver-utils dbus-bin
```

The **PLAYER TUNING BLOCK** in `src/config.mjs` holds the values measured on the
Pi 4 (hwdec, video output, format selector, audio device, yt-dlp options);
re-measure before changing them. `mpvArguments()` in that
same file is the only argv builder. It uses mpv 0.35's `loadfile` options syntax.

## Cast from the Mac

`vantv` (`macbook/scripts/vantv.zsh`) casts a YouTube link (watch, youtu.be,
shorts, live or embed, honouring `t=`), or with no argument the front
Chrome/Safari YouTube tab, then the clipboard; `vantv pause|resume|stop|status`
drives playback. The Firefox extension in `macbook/firefox/van-tv/` adds a
Van TV button to YouTube's player, a context-menu entry on video links and a
toolbar popup. Both talk to the status server directly over the LAN
(`/api/cast`, `/api/control`, `/api/status`); no YouTube pairing is involved.
Chrome's own cast button is Google Cast only, so it never lists Van TV.

## Mac BTT media buttons

The BTT Media menu runs Pi shell commands over ssh (`vlcmd Seek int64:±N`, `pp`,
`vlcmd Next/Previous`, `kill_media`, `play_status`). `pi/.bashrc` routes them to
Van TV through `/api/control` while a cast is loaded (player LOADING, PLAYING or
PAUSED), and to VLC otherwise, so the BTT configuration needs no change.

## Pair the phone

### Phone on dendelion

1. In the iPhone YouTube app, tap Cast and select **Van TV**.
2. Allow YouTube's iOS Local Network permission if discovery is unavailable.
3. Choose a video. The phone supplies play/pause, seeking, volume and queue controls.

Discovery needs LAN multicast SSDP (UDP 1900) and TCP DIAL port **8794**.
This is YouTube DIAL/Lounge, not a general Google Cast receiver or AirPlay.

### Phone is the internet hotspot

1. On the Mac, open `http://vanpi.lan:8795/` to read **Link with TV code**.
2. On the iPhone: YouTube → Settings → Watch on TV → Enter TV code.
3. Enter the displayed 12 digits. After pairing, select **Van TV** from Cast.

The phone need not reach the Pi directly for this pairing/control path; both
need internet. The Mac can remain on dendelion to read the code while the phone
provides its upstream hotspot. Codes refresh every five minutes; the page's
button requests a fresh one. A code is temporary, but the paired screen identity
is permanent. Do not delete the private state directory to update the app.

## Ports, state and service

- DIAL: **8794**. Upstream exposes no route-mounting API, so the status server is
  separate on **8795**, bound to Pi LAN address and loopback only.
- Status page: `http://vanpi.lan:8795/`; JSON health: `/health`; page data: `/api/status`.
  `POST /api/cast` (JSON `{"videoId", "position"}`) starts a video at once and
  answers 202 while it loads; `POST /api/control` (JSON `{"action":
  "pause"|"resume"|"toggle"|"stop"|"next"|"previous"}`, or `{"action": "seek"}`
  with exactly one of an absolute `position` or a relative `offset` in seconds,
  clamped to the video) drives playback. Stop also clears the queue so the same
  video can be recast; with nothing queued, Next does nothing and Previous
  restarts the video. A POST needs `Content-Type: application/json`
  and an `Origin` that is absent, `null`, an extension's, or the server's own:
  web pages can't forge those, and the JSON type forces a CORS preflight the
  server never answers.
  Health contains no pairing code, token or screen identity.
- `src/config.mjs` is the single definition of ports, LAN address, paths,
  playback options and SponsorBlock categories.
- Private state: `/home/pi/.local/state/youtube-cast/`, mode 700; JSON files 600.
  `app.pid.json`, `mdxContext.cl.json` (YouTube), `mdxContext.m.json` (Music) belong to the
  upstream `DataStore` API. `settings.json` preserves receiver volume/mute.
  Pairings are held by YouTube against the persisted screen ID, not a separate
  local list of phone credentials. Corrupt local identity files fail closed.
- Runtime mpv sockets and cache: `/run/youtube-cast/` (systemd-owned lifecycle).
- Service: `youtube-cast.service`, User/Group `pi`, boot-enabled system unit,
  failure restart after 5 seconds, 20-second shutdown budget, umask 0077.
- X11, ALSA/DRM and the user's D-Bus stay accessible. Filesystem writes are
  restricted to receiver state and runtime. PrivateTmp/PrivateDevices are
  deliberately off for the display and audio devices.

The status page and pairing codes are for trusted LAN users, with no login.
Do not forward either port to the public internet. Upstream `bindToAddresses`
restricts SSDP interfaces, **not** its HTTP listener: DIAL still listens on all
interfaces, including any Tailscale interface. No Tailscale publication or ACL
change is made by this project. If stronger isolation is needed, enforce it in
the host firewall separately. The status server rejects foreign Host headers
and cross-origin refresh requests.

## Lifecycle and maintenance

Use `bash pi/projects/youtube-cast/deploy.sh <verb>` from the Mac:

| Verb | Effect |
| --- | --- |
| `prepare` | Check Pi prerequisites, JS syntax and `systemd-analyze verify`; no registration |
| `install` | Install new release/dependencies/unit, enable, restart, require health |
| `status` | Check enabled/active, root-owned unit content, MainPID, health PID and DIAL listener |
| `start` | Enable and start a deliberately stopped installed service |
| `stop` | `disable --now`; keep code and pairing identity |
| `restart` | Restart installed service without changing boot registration |
| `verify` | Kill main process and require a new healthy PID, proving failure recovery |
| `uninstall` | Disable/stop and remove registration only; retain code, venv, state |
| `update-ytdlp` | Upgrade latest `yt-dlp[default]` and Deno; no service restart needed |

On the Pi, the same verbs are available through:

```sh
bash /home/pi/youtube-cast/current/controller.sh status
journalctl -u youtube-cast.service -n 80 --no-pager
```

YouTube regularly breaks old extractors. Run `update-ytdlp` when resolution
fails, preferably while idle. Suggested **pi-user** crontab entry (proposal
only; the deployer never installs it):

```cron
0 6 * * 0 /bin/bash /home/pi/youtube-cast/current/controller.sh update-ytdlp
```

For a release rollback, inspect the printed retained release and `previous`,
point `current` back to that release, reinstall that release's unit as a
root-owned regular file, daemon-reload, restart and run `status`. Do not delete
or restore an older screen identity as part of a code rollback. yt-dlp's shared
venv is independent of release rollback.

## Playback ownership

Before a video, POST `action=stop` to the video library's `/api/control` on
127.0.0.1:8789. That route bookmarks the movie, then sends Stop and Quit to VLC.
Only if unavailable does the adapter fall back to MPRIS Quit followed by
stopping the library's `van-video-player.service` user unit. Failure of both
paths prevents a competing cast window. The fallback cannot promise the same
bookmark checkpoint as the HTTP route.

`xset -display :0 dpms force on` wakes the screen each video. On the first video
of a cast session, `/home/pi/sns.sh rear_movie` switches rear Sonos to optical.

Audio path: the Pi's HDMI output runs through a hardware splitter box that
extracts the audio to optical, into the Sonos soundbar; `rear_movie` puts the
soundbar on its optical input and groups vonRear with it. So mpv plays to ALSA
HDMI 0 (`hdmi:CARD=vc4hdmi0,DEV=0`) directly: PipeWire shows only a null sink
because the display chain reports no audio sink, and nothing on the Pi mixes or
routes the sound. Volume is the Sonos's.
It runs in its own process group, with an 18-second timeout and bounded kill;
failures are logged and playback continues. Stopping/disconnecting closes mpv
and its resolver children so the desktop returns. Other senders may stay
connected; playback stops when the last sender disconnects. Starting VLC later
does not yet tell this receiver to stop: avoid simultaneous starts from two apps.

## Implementation and tests

`yt-cast-receiver` is pinned to **2.1.0** and the lockfile is required. The
adapter implements all nine `Player.do*` methods, uses public player controls
and `notifyExternalStateChange`, advances the queue on EOF, and cancels pending
loads on stop/replacement. Pairing uses `getPairingCodeRequestService()` with
`request`/`response`/`error`, `start()`/`stop()`. `PrivateDataStore extends
DataStore` supplies atomic private `get`/`set` persistence.

Security overrides pin `query-string` 9.5.1 and `uuid` 11.1.1 to avoid the
upstream dependency tree's moderate decode/UUID advisories; upstream uses their
compatible parse/stringify and v4 APIs. Re-test DIAL and cloud pairing when
upgrading either override or the receiver library.

```sh
npm --prefix pi/projects/youtube-cast ci
npm --prefix pi/projects/youtube-cast test
.local/test-venv/bin/python run_tests.py
```

The repository runner already discovers `.test.mjs` files and runs each with
Node's built-in test runner. Tests use a real fake Unix IPC socket, real public
receiver/player and queue methods with fake mpv, SponsorBlock responses, HTTP
status requests, private-store reopen, room preparation and VLC handoff fakes.
No test switches speakers or stops a real player.

Modules: `index.mjs` wires; `receiver-lifecycle.mjs` supervises; `mpv-ipc.mjs`
speaks JSON IPC; `mpv-process.mjs` owns mpv/yt-dlp process groups;
`mpv-player.mjs` adapts the receiver API; `sponsor-block.mjs` fetches/skips;
`room-preparation.mjs` and `vlc-handoff.mjs` own screen/audio handoff;
`process-command.mjs` is their shared bounded subprocess boundary;
`pairing-codes.mjs`, `private-data-store.mjs`, `status-server.mjs`, and `web/`
own pairing, persistence and the status UI.

## Limitations and acceptance checks

Upstream labels the unofficial Lounge implementation work-in-progress.
Persistent screen IDs preserve pairings across normal restarts, but Google can
invalidate them; upstream may replace an invalid screen ID. `stable-dial-uuid.mjs` pins
the SSDP device UUID (upstream regenerates it on restart); `dial.uuid.json`
holds it in the state directory. Autoplay/queue lookup still uses
upstream YouTube.js. Sign-in-required, private, age-restricted and regional
videos are not guaranteed; this adapter does not use phone CTT credentials.
SponsorBlock is best-effort and cannot remove segments that have not been
submitted. YouTube server-side changes can affect extraction and pairing.

Verified from the Mac on 2026-10-21: LAN discovery, TV-code pairing (ytcast),
direct `/api/cast` from `vantv`, pause/resume/stop, recast after stop, rear
Sonos audio, service restart without re-pairing. Still to verify with the real phone on both networks: discovery;
TV-code pairing; play/pause/seek/volume; queue add/next and automatic EOF;
service restart without re-pairing; stop during loading; last-phone disconnect;
VLC resume-position preservation; fullscreen/audio quality; SponsorBlock skip.
`verify` proves systemd recovery, not a reboot or authenticated iPhone control.
