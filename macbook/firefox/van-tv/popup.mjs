import './config.js';
import { createScrubState, formatTime, popupState, transitionScrub } from './popup-state.mjs';

const config = globalThis.VAN_TV_CONFIG;
const ids = [
  'state', 'video', 'elapsed', 'duration', 'scrub', 'notice', 'cast-tab',
  'previous', 'back-five', 'back-twenty', 'toggle', 'forward-twenty',
  'forward-five', 'next', 'stop', 'restart', 'play-icon', 'pause-icon',
];
const element = Object.fromEntries(ids.map(id => [id, document.getElementById(id)]));
const transportControls = [
  { button: element.previous, action: config.control.previous },
  { button: element['back-five'], action: config.control.seek,
    fields: { offset: config.skipOffsets.backwardFiveMinutes } },
  { button: element['back-twenty'], action: config.control.seek,
    fields: { offset: config.skipOffsets.backwardTwentySeconds } },
  { button: element.toggle, action: config.control.toggle },
  { button: element['forward-twenty'], action: config.control.seek,
    fields: { offset: config.skipOffsets.forwardTwentySeconds } },
  { button: element['forward-five'], action: config.control.seek,
    fields: { offset: config.skipOffsets.forwardFiveMinutes } },
  { button: element.next, action: config.control.next },
  { button: element.stop, action: config.control.stop },
  { button: element.restart, action: config.control.seek, fields: { position: 0 } },
];

let latestStatus;
let scrub = createScrubState();
let busy = false;
let polling = false;
let tabAvailable = false;
let pollTimer;
let progressTimer;
let progressTime = performance.now();
let closed = false;
let showRemaining = false;

async function send(type, fields = {}) {
  const response = await browser.runtime.sendMessage({ type, ...fields });
  if (!response?.ok) throw new Error(response?.error || 'Van TV request failed');
  return response.data;
}

function renderPlayback() {
  const duration = latestStatus?.duration ?? 0;
  if (!scrub.dragging) {
    element.scrub.max = String(duration);
    element.scrub.value = String(scrub.position);
  }
  element.elapsed.textContent = formatTime(scrub.position);
  element.duration.textContent = latestStatus
    ? (showRemaining ? `−${formatTime(Math.max(0, duration - scrub.position))}` : formatTime(duration))
    : '—';
  element.scrub.setAttribute('aria-valuetext', `${formatTime(scrub.position)} of ${formatTime(duration)}`);
}

function updateControls() {
  const view = latestStatus ? popupState(latestStatus) : null;
  for (const item of transportControls) {
    item.button.disabled = busy || !view?.actions.includes(item.action);
  }
  const toggleLabel = view?.toggleLabel ?? 'Play';
  element['play-icon'].toggleAttribute('hidden', toggleLabel === 'Pause');
  element['pause-icon'].toggleAttribute('hidden', toggleLabel !== 'Pause');
  element.toggle.title = toggleLabel;
  element.toggle.setAttribute('aria-label', toggleLabel);
  if (!scrub.dragging) element.scrub.disabled = busy || !view?.scrubEnabled;
  element['cast-tab'].disabled = busy || !tabAvailable;
  element.duration.disabled = !view?.scrubEnabled;
}

function renderStatus() {
  if (latestStatus) {
    const view = popupState(latestStatus);
    element.state.textContent = view.state === latestStatus.player
      ? view.state[0] + view.state.slice(1).toLowerCase() : view.state;
    element.video.textContent = view.title;
  } else {
    element.state.textContent = 'Van TV unreachable';
    element.video.textContent = '';
  }
  renderPlayback();
  updateControls();
}

async function refreshStatus() {
  try {
    const status = await send(config.message.status);
    scrub = transitionScrub(scrub, {
      type: 'poll', position: status.position, duration: status.duration,
    }).state;
    latestStatus = status;
  } catch {
    latestStatus = null;
    if (!scrub.dragging) scrub = createScrubState();
  }
  progressTime = performance.now();
  renderStatus();
}

async function refreshTab() {
  try { tabAvailable = Boolean(await send(config.message.tabVideo)); }
  catch { tabAvailable = false; }
  updateControls();
}

async function poll() {
  if (closed || polling) return;
  polling = true;
  try { await Promise.all([refreshStatus(), refreshTab()]); }
  finally {
    polling = false;
    if (!closed) pollTimer = setTimeout(poll, config.pollIntervalMs);
  }
}

async function perform(type, fields, success) {
  if (busy) return;
  busy = true;
  element.notice.textContent = '';
  updateControls();
  try {
    const data = await send(type, fields);
    element.notice.textContent = data.done === false ? 'No playback change' : success;
    await refreshStatus();
  } catch (error) { element.notice.textContent = error.message; }
  finally { busy = false; updateControls(); }
}

function updateProgress() {
  const now = performance.now();
  const seconds = (now - progressTime) / 1000;
  progressTime = now;
  if (!latestStatus?.ok || latestStatus.player !== config.player.playing) return;
  scrub = transitionScrub(scrub, {
    type: 'advance', seconds, duration: latestStatus.duration,
  }).state;
  renderPlayback();
}

for (const item of transportControls) {
  item.button.addEventListener('click', () => void perform(config.message.control,
    { action: item.action, ...(item.fields ?? {}) }, ''));
}

element.scrub.addEventListener('input', event => {
  scrub = transitionScrub(scrub, {
    type: 'input', position: Number(event.currentTarget.value),
    duration: latestStatus?.duration,
  }).state;
  renderPlayback();
});

element.scrub.addEventListener('change', event => {
  const result = transitionScrub(scrub, {
    type: 'change', position: Number(event.currentTarget.value),
    duration: latestStatus?.duration,
  });
  scrub = result.state;
  if (latestStatus) latestStatus = { ...latestStatus, position: scrub.position };
  progressTime = performance.now();
  renderStatus();
  void perform(config.message.control, { action: config.control.seek, ...result.seek }, '');
});

element['cast-tab'].addEventListener('click', () => void perform(
  config.message.castTab, {}, 'Cast accepted'));
element.duration.addEventListener('click', () => {
  showRemaining = !showRemaining;
  const label = showRemaining ? 'Show total time' : 'Show remaining time';
  element.duration.title = label;
  element.duration.setAttribute('aria-label', label);
  element.duration.setAttribute('aria-pressed', String(showRemaining));
  renderPlayback();
});
window.addEventListener('pagehide', () => {
  closed = true;
  clearTimeout(pollTimer);
  clearInterval(progressTimer);
});
void browser.browserAction.setBadgeText({ text: '' });
void browser.browserAction.setTitle({ title: 'Van TV' });
progressTimer = setInterval(updateProgress, config.progressIntervalMs);
void poll();
