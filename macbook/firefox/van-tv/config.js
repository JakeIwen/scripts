// A classic script lets content scripts share these constants without exposing
// extension modules as web-accessible resources. ES modules import it for effect.
globalThis.VAN_TV_CONFIG = Object.freeze({
  receiverUrls: Object.freeze(['http://vanpi.lan:8795', 'http://192.168.6.103:8795']),
  requestTimeoutMs: 3000,
  pollIntervalMs: 2000,
  progressIntervalMs: 250,
  feedbackDurationMs: 3500,
  youtube: Object.freeze({
    hosts: Object.freeze(['www.youtube.com', 'youtube.com', 'm.youtube.com']),
    shortHosts: Object.freeze(['youtu.be', 'www.youtu.be']),
    contentMatches: Object.freeze(['*://www.youtube.com/*']),
    watchPath: '/watch',
    videoPaths: Object.freeze(['/shorts/', '/live/', '/embed/']),
    videoIdPattern: /^[A-Za-z0-9_-]{11}$/,
  }),
  message: Object.freeze({
    cast: 'cast', castTab: 'cast-tab', tabVideo: 'tab-video',
    status: 'status', control: 'control', getVideo: 'get-video',
    castAccepted: 'cast-accepted',
  }),
  player: Object.freeze({
    idle: 'IDLE', loading: 'LOADING', playing: 'PLAYING',
    paused: 'PAUSED', stopped: 'STOPPED',
  }),
  control: Object.freeze({
    pause: 'pause', resume: 'resume', toggle: 'toggle', stop: 'stop',
    next: 'next', previous: 'previous', seek: 'seek',
  }),
  skipOffsets: Object.freeze({
    backwardFiveMinutes: -300, backwardTwentySeconds: -20,
    forwardTwentySeconds: 20, forwardFiveMinutes: 300,
  }),
  command: 'cast-active-tab',
});
