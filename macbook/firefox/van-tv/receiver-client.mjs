import './config.js';
import { isPosition, isVideoId } from './youtube-video.mjs';

const config = globalThis.VAN_TV_CONFIG;
const states = Object.values(config.player);
const actions = Object.values(config.control);

/** @typedef {{ok: boolean, player: string, video: {id: string, title: string}|null,
 * position: number, duration: number}} ReceiverStatus */

export function decodeStatus(value) {
  if (!value || typeof value.ok !== 'boolean' || !states.includes(value.player)
      || !isPosition(value.position) || !isPosition(value.duration)
      || (value.video !== null && (!value.video || !isVideoId(value.video.id)
        || typeof value.video.title !== 'string'))) {
    throw new Error('Invalid Van TV status response');
  }
  const { ok, player, video, position, duration } = value;
  return { ok, player, video, position, duration };
}

function controlBody(action, fields) {
  if (!actions.includes(action)) throw new Error('Unknown playback action');
  if (action !== config.control.seek) return { action };
  if (!fields || typeof fields !== 'object' || Array.isArray(fields)) {
    throw new Error('Invalid seek fields');
  }
  const hasPosition = Object.hasOwn(fields, 'position');
  const hasOffset = Object.hasOwn(fields, 'offset');
  const validPosition = hasPosition && isPosition(fields.position);
  const validOffset = hasOffset && typeof fields.offset === 'number' && Number.isFinite(fields.offset);
  if (hasPosition === hasOffset || !validPosition && !validOffset) throw new Error('Invalid seek fields');
  return hasPosition ? { action, position: fields.position } : { action, offset: fields.offset };
}

async function requestJson(fetcher, url, options, timeoutMs) {
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), timeoutMs);
  try {
    const response = await fetcher(url, {
      ...options, signal: abort.signal, cache: 'no-store', credentials: 'omit', redirect: 'error',
    });
    if (!response.ok) {
      if (response.status === 503) throw new Error('Van TV receiver is not running');
      throw new Error(`Van TV request failed (HTTP ${response.status})`);
    }
    return await response.json();
  } finally { clearTimeout(timer); }
}

export function createReceiverClient({ fetcher = globalThis.fetch,
  baseUrls = config.receiverUrls, timeoutMs = config.requestTimeoutMs } = {}) {
  let preferred;

  async function status() {
    const urls = [...new Set([preferred, ...baseUrls].filter(Boolean))];
    for (const base of urls) {
      try {
        const result = decodeStatus(await requestJson(fetcher, `${base}/api/status`, {}, timeoutMs));
        preferred = base;
        return result;
      } catch (error) {
        if (!(error instanceof TypeError) && error.name !== 'AbortError') throw error;
      }
    }
    throw new Error('Van TV unreachable');
  }

  async function post(path, body) {
    // Probe the read endpoint for failover before a write. A lost POST response
    // is ambiguous, so never replay that write through the second address.
    await status();
    try {
      return await requestJson(fetcher, `${preferred}${path}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      }, timeoutMs);
    } catch (error) {
      if (error instanceof TypeError || error.name === 'AbortError') {
        throw new Error('Van TV unreachable; request may have been accepted');
      }
      throw error;
    }
  }

  async function cast(videoId, position = 0) {
    if (!isVideoId(videoId) || !isPosition(position)) throw new Error('Invalid YouTube video or position');
    const result = await post('/api/cast', { videoId, position });
    if (result?.videoId !== videoId || !isPosition(result.position)) {
      throw new Error('Invalid Van TV cast response; check playback before retrying');
    }
    return result;
  }

  async function control(action, fields = {}) {
    const body = controlBody(action, fields);
    const result = await post('/api/control', body);
    if (result?.action !== action || typeof result.done !== 'boolean') {
      throw new Error('Invalid Van TV control response; check playback before retrying');
    }
    return result;
  }

  return { status, cast, control };
}
