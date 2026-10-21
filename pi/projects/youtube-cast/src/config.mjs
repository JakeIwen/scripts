import { join } from 'node:path';

export const config = Object.freeze({
  name: 'Van TV',
  lanAddress: process.env.YOUTUBE_CAST_LAN_ADDRESS || '192.168.6.103',
  dialPort: 8794,
  statusPort: 8795,
  stateDirectory: '/home/pi/.local/state/youtube-cast',
  runtimeDirectory: '/run/youtube-cast',
  installDirectory: '/home/pi/youtube-cast',
  videoLibraryUrl: 'http://127.0.0.1:8789/api/control',
  sponsorBlockUrl: 'https://sponsor.ajay.app/api/skipSegments',
  sponsorBlockCategories: ['sponsor', 'selfpromo'],
  sessionEnvironment: {
    DISPLAY: ':0',
    XDG_RUNTIME_DIR: '/run/user/1000',
    DBUS_SESSION_BUS_ADDRESS: 'unix:path=/run/user/1000/bus',
  },
  roomCommand: ['/bin/bash', '/home/pi/sns.sh', 'rear_movie'],
  wakeCommand: ['/usr/bin/xset', '-display', ':0', 'dpms', 'force', 'on'],
  vlcQuitCommand: ['/usr/bin/dbus-send', '--session', '--type=method_call',
    '--print-reply', '--dest=org.mpris.MediaPlayer2.vlc', '/org/mpris/MediaPlayer2',
    'org.mpris.MediaPlayer2.Quit'],
  vlcActiveCommand: ['/usr/bin/systemctl', '--user', 'is-active', '--quiet', 'van-video-player.service'],
  vlcStopCommand: ['/usr/bin/systemctl', '--user', 'stop', 'van-video-player.service'],
  roomTimeoutMs: 18_000,
  loadTimeoutMs: 90_000,
  pairingLifetimeMs: 300_000,
  pairingRetryMs: 30_000,
});

// PLAYER TUNING BLOCK: measured on the Pi 4 (2026-10-21). xv beat gpu on dropped
// frames, and 60 fps or 1080p30 can't be presented smoothly, so the selector
// steps down to H.264 720p30 or 480p. PipeWire exposes only a null sink, so
// audio goes straight to ALSA HDMI 0, which carries it to the rear Sonos optical.
// yt-dlp needs Node >= 22 or Deno for YouTube's JS challenges; the venv ships Deno.
export const playback = Object.freeze({
  mpv: '/usr/bin/mpv',
  ytdlp: join(config.installDirectory, 'venv/bin/yt-dlp'),
  format: 'bv*[vcodec^=avc1][height=1080][fps<=25]+ba[acodec^=mp4a]/'
    + 'bv*[vcodec^=avc1][height<=720][fps<=30]+ba[acodec^=mp4a]/'
    + 'bv*[vcodec^=avc1][height<=480]+ba[acodec^=mp4a]/'
    + 'bv*[vcodec^=vp9][height<=720][fps<=30]+ba/b[height<=720]/best',
  args: ['--no-config', '--fullscreen', '--idle=yes', '--force-window=no',
    '--keep-open=no', '--no-terminal', '--osd-level=0', '--no-osc',
    '--no-input-default-bindings', '--hwdec=v4l2m2m-copy', '--vo=xv', '--ao=alsa',
    '--audio-device=alsa/hdmi:CARD=vc4hdmi0,DEV=0', '--cache=yes',
    '--demuxer-readahead-secs=20', '--demuxer-max-bytes=96MiB'],
  ytdlpOptions: `no-playlist=,js-runtimes=deno:${join(config.installDirectory, 'venv/bin/deno')}`,
});

export function mpvArguments(socketPath) {
  return [...playback.args, `--input-ipc-server=${socketPath}`,
    `--ytdl-format=${playback.format}`,
    `--script-opts=ytdl_hook-ytdl_path=${playback.ytdlp}`,
    `--ytdl-raw-options=${playback.ytdlpOptions}`];
}
