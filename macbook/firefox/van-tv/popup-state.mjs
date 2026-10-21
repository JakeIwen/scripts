import './config.js';

const { player, control } = globalThis.VAN_TV_CONFIG;
const availableActions = Object.freeze({
  [player.idle]: [],
  [player.loading]: [control.stop],
  [player.playing]: [control.pause, control.stop],
  [player.paused]: [control.resume, control.stop],
  [player.stopped]: [],
});

export function formatTime(value) {
  if (!Number.isFinite(value) || value < 0) return '—';
  const seconds = Math.floor(value);
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor(seconds % 3600 / 60);
  const remainder = String(seconds % 60).padStart(2, '0');
  return hours ? `${hours}:${String(minutes).padStart(2, '0')}:${remainder}` : `${minutes}:${remainder}`;
}

export function popupState(status) {
  return {
    state: status.ok ? status.player : 'Receiver not running',
    title: status.video?.title || (status.video ? status.video.id : 'No video'),
    time: `${formatTime(status.position)} / ${formatTime(status.duration)}`,
    actions: status.ok ? (availableActions[status.player] ?? []) : [],
  };
}
