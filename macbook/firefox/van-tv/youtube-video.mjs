import './config.js';

const { youtube } = globalThis.VAN_TV_CONFIG;

/** @typedef {{videoId: string, position: number}} VideoSelection */
/** @typedef {{videoId: string, position: number|null}} VideoObservation */

export function isVideoId(value) {
  return typeof value === 'string' && youtube.videoIdPattern.test(value);
}

export function isPosition(value) {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0;
}

export function parseStartTime(value) {
  if (typeof value !== 'string' || !value) return 0;
  if (/^\d+(?:\.\d+)?$/.test(value)) {
    const seconds = Number(value);
    return isPosition(seconds) ? seconds : 0;
  }
  const units = /^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?$/i.exec(value);
  if (!units) return 0;
  const seconds = Number(units[1] || 0) * 3600
    + Number(units[2] || 0) * 60 + Number(units[3] || 0);
  return isPosition(seconds) ? seconds : 0;
}

/** @returns {VideoSelection|null} */
export function parseYouTubeVideo(value) {
  let url;
  try { url = new URL(value); } catch { return null; }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.port) return null;
  let videoId;
  if (youtube.shortHosts.includes(url.hostname)) {
    videoId = url.pathname.replace(/^\//, '').replace(/\/$/, '');
  } else if (youtube.hosts.includes(url.hostname)) {
    if (url.pathname === youtube.watchPath) videoId = url.searchParams.get('v');
    else {
      const prefix = youtube.videoPaths.find(path => url.pathname.startsWith(path));
      if (prefix) videoId = url.pathname.slice(prefix.length).replace(/\/$/, '');
    }
  }
  if (!isVideoId(videoId)) return null;
  const fragment = new URLSearchParams(url.hash.slice(1));
  const start = url.searchParams.get('t') ?? url.searchParams.get('start') ?? fragment.get('t');
  return { videoId, position: parseStartTime(start) };
}

export function youtubeMenuPatterns() {
  return {
    page: youtube.hosts.map(host => `*://${host}${youtube.watchPath}?*`),
    link: [
      ...youtube.hosts.flatMap(host => [
        `*://${host}${youtube.watchPath}?*`,
        ...youtube.videoPaths.map(path => `*://${host}${path}*`),
      ]),
      ...youtube.shortHosts.map(host => `*://${host}/*`),
    ],
  };
}
