import './config.js';

const { player, control } = globalThis.VAN_TV_CONFIG;
const playbackActions = Object.freeze([
  control.previous, control.seek, control.toggle, control.next, control.stop,
]);
const loadingActions = Object.freeze([control.stop]);
const availableActions = Object.freeze({
  [player.idle]: Object.freeze([]),
  [player.loading]: loadingActions,
  [player.playing]: playbackActions,
  [player.paused]: playbackActions,
  [player.stopped]: Object.freeze([]),
});

export function formatTime(value) {
  if (!Number.isFinite(value) || value < 0) return '—';
  const seconds = Math.floor(value);
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor(seconds % 3600 / 60);
  const remainder = String(seconds % 60).padStart(2, '0');
  return hours ? `${hours}:${String(minutes).padStart(2, '0')}:${remainder}` : `${minutes}:${remainder}`;
}

function boundedPosition(value, duration) {
  const position = Number.isFinite(value) && value >= 0 ? value : 0;
  return Number.isFinite(duration) && duration >= 0 ? Math.min(position, duration) : position;
}

export function createScrubState(position = 0, duration = Infinity) {
  return { dragging: false, position: boundedPosition(position, duration) };
}

export function transitionScrub(state, event) {
  if (event.type === 'poll') {
    if (state.dragging) return { state, seek: null };
    return { state: createScrubState(event.position, event.duration), seek: null };
  }
  if (event.type === 'input') {
    return {
      state: { dragging: true, position: boundedPosition(event.position, event.duration) },
      seek: null,
    };
  }
  if (event.type === 'change') {
    const position = boundedPosition(event.position, event.duration);
    return { state: { dragging: false, position }, seek: { position } };
  }
  if (event.type === 'advance') {
    if (state.dragging || !Number.isFinite(event.seconds) || event.seconds <= 0) {
      return { state, seek: null };
    }
    return {
      state: { dragging: false,
        position: boundedPosition(state.position + event.seconds, event.duration) },
      seek: null,
    };
  }
  throw new Error('Unknown scrub transition');
}

// Signed whole milliseconds, e.g. "+120 ms" or "0 ms".
export function formatAudioDelay(seconds) {
  if (!Number.isFinite(seconds)) return '—';
  const ms = Math.round(seconds * 1000) || 0;
  return `${ms > 0 ? '+' : ''}${ms} ms`;
}

export function popupState(status) {
  const actions = status.ok ? (availableActions[status.player] ?? []) : [];
  const elapsed = formatTime(status.position);
  const total = formatTime(status.duration);
  return {
    state: status.ok ? status.player : 'Receiver not running',
    title: status.video?.title || (status.video ? status.video.id : 'No video'),
    elapsed,
    total,
    time: `${elapsed} / ${total}`,
    actions,
    scrubEnabled: Boolean(status.ok && status.video && status.duration > 0
      && actions.includes(control.seek)),
    toggleLabel: status.player === player.playing ? 'Pause' : 'Play',
    // The offset is saved on the receiver, so it can be set before a cast too.
    audioDelayEnabled: Boolean(status.ok && Number.isFinite(status.audioDelay)),
    audioDelayLabel: formatAudioDelay(status.audioDelay),
  };
}
