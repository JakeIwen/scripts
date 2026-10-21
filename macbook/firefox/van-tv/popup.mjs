import './config.js';
import { popupState } from './popup-state.mjs';

const config = globalThis.VAN_TV_CONFIG;
const element = Object.fromEntries(['state', 'video', 'time', 'notice', 'cast-tab', 'pause', 'resume', 'stop']
  .map(id => [id, document.getElementById(id)]));
const controls = Object.values(config.control);
let latestStatus;
let busy = false;
let polling = false;
let tabAvailable = false;
let timer;
let closed = false;

async function send(type, fields = {}) {
  const response = await browser.runtime.sendMessage({ type, ...fields });
  if (!response?.ok) throw new Error(response?.error || 'Van TV request failed');
  return response.data;
}

function updateButtons() {
  const actions = latestStatus ? popupState(latestStatus).actions : [];
  for (const action of controls) element[action].disabled = busy || !actions.includes(action);
  element['cast-tab'].disabled = busy || !tabAvailable;
}

async function refreshStatus() {
  try {
    latestStatus = await send(config.message.status);
    const view = popupState(latestStatus);
    element.state.textContent = view.state;
    element.video.textContent = view.title;
    element.time.textContent = view.time;
  } catch {
    latestStatus = null;
    element.state.textContent = 'Van TV unreachable';
    element.video.textContent = '';
    element.time.textContent = '';
  }
  updateButtons();
}

async function refreshTab() {
  try { tabAvailable = Boolean(await send(config.message.tabVideo)); }
  catch { tabAvailable = false; }
  updateButtons();
}

async function poll() {
  if (closed || polling) return;
  polling = true;
  try { await Promise.all([refreshStatus(), refreshTab()]); }
  finally {
    polling = false;
    if (!closed) timer = setTimeout(poll, config.pollIntervalMs);
  }
}

async function perform(type, fields, success) {
  if (busy) return;
  busy = true;
  element.notice.textContent = '';
  updateButtons();
  try {
    const data = await send(type, fields);
    element.notice.textContent = data.done === false ? 'No playback change' : success;
    await refreshStatus();
  } catch (error) { element.notice.textContent = error.message; }
  finally { busy = false; updateButtons(); }
}

for (const action of controls) {
  element[action].addEventListener('click', () => void perform(config.message.control, { action }, ''));
}
element['cast-tab'].addEventListener('click', () => void perform(config.message.castTab, {}, 'Cast accepted'));
window.addEventListener('pagehide', () => { closed = true; clearTimeout(timer); });
void browser.browserAction.setBadgeText({ text: '' });
void browser.browserAction.setTitle({ title: 'Van TV' });
void poll();
