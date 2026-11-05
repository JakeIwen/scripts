// Shared UI parts for every section: one toggle, one labelled-control row, one tag, one time, one info icon.
import {PLACEHOLDER, armed, button, confirmText, node} from './dom_core.mjs';
import {absoluteText, parseStamp, relativeText} from './relative_time.mjs';

const textOf = value => value instanceof Node ? value : document.createTextNode(String(value ?? PLACEHOLDER));

export function anchor(element, id) {
  element.dataset.anchor = id;
  return element;
}

export function tag(text, level) {
  return node('span', text, level ? `tag level-${level}` : 'tag');
}

// Times show relative text with the exact local time as a tooltip; `placeholder` words a missing one ("never").
export function timeNode(value, options = {}) {
  const placeholder = typeof options === 'string' ? options : options.placeholder ?? PLACEHOLDER;
  const ms = parseStamp(value);
  if (ms === null) return node('span', value ? String(value) : placeholder, 'muted');
  const result = node('time', relativeText(ms));
  result.dateTime = new Date(ms).toISOString();
  result.dataset.ms = ms;
  result.title = absoluteText(ms);
  return result;
}

export function timeWithAbsolute(value) {
  const ms = parseStamp(value);
  const result = node('div');
  result.append(timeNode(value));
  if (ms !== null) result.append(node('div', absoluteText(ms), 'muted small'));
  return result;
}

export function refreshTimes(root = document) {
  root.querySelectorAll('time[data-ms]').forEach(element => {
    element.textContent = relativeText(Number(element.dataset.ms));
  });
}

export function emptyState(text) {
  return node('p', text, 'empty');
}

export function fieldError(message) {
  const result = node('p', message, 'field-error');
  result.setAttribute('role', 'alert');
  return result;
}

export function loadError(message, retry) {
  const result = node('div', null, 'load-error');
  result.setAttribute('role', 'alert');
  result.append(node('span', message));
  if (retry) result.append(button('Retry', retry, {key: 'retry'}));
  return result;
}

export function kv(rows) {
  const list = node('dl', null, 'kv');
  rows.forEach(([term, value]) => {
    if (value === undefined) return;
    const definition = node('dd');
    definition.append(textOf(value));
    list.append(node('dt', term), definition);
  });
  return list;
}

export function card({title, level, summary, body, actions, anchor: id}) {
  const result = node('article', null, 'card');
  const head = node('div', null, 'card-head');
  const heading = node('h3');
  if (level) heading.append(node('span', null, `dot level-${level}`));
  heading.append(title instanceof Node ? title : document.createTextNode(String(title)));
  head.append(heading);
  if (summary) head.append(node('span', summary, 'muted card-summary'));
  if (actions) head.append(actions);
  result.append(head);
  if (body) result.append(body);
  return id ? anchor(result, id) : result;
}

// ---- info icon: hover/focus tooltip from data-tip (CSS attr()), click popover for touch and keyboard ----

const openInfos = new Set();
let infoListeners = false;

function closeInfos(except) {
  openInfos.forEach(item => {
    if (item === except) return;
    item.button.setAttribute('aria-expanded', 'false');
    item.pop.hidden = true;
    openInfos.delete(item);
  });
}

function listenForDismissal() {
  if (infoListeners) return;
  infoListeners = true;
  document.addEventListener('click', event => {
    if (![...openInfos].some(item => item.root.contains(event.target))) closeInfos(null);
  });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || !openInfos.size) return;
    const [first] = openInfos;
    first.button.focus();
    closeInfos(null);
  });
}

export function info(text) {
  listenForDismissal();
  const root = node('span', null, 'info');
  const trigger = node('button', 'i', 'info-button');
  trigger.type = 'button';
  trigger.setAttribute('aria-label', 'More information');
  trigger.setAttribute('aria-expanded', 'false');
  trigger.dataset.tip = text;
  const pop = node('span', text, 'info-pop');
  pop.setAttribute('role', 'note');
  pop.hidden = true;
  const item = {root, button: trigger, pop};
  trigger.addEventListener('click', () => {
    const opening = pop.hidden;
    closeInfos(item);
    pop.hidden = !opening;
    trigger.setAttribute('aria-expanded', String(opening));
    if (opening) {
      openInfos.add(item);
      // Keep the popover inside the viewport whichever side the icon is on (CSSOM, so no inline-style attribute).
      const edge = root.getBoundingClientRect().left;
      pop.style.left = `${Math.max(8, Math.min(edge, innerWidth - pop.offsetWidth - 8)) - edge}px`;
    } else {
      openInfos.delete(item);
    }
  });
  root.append(trigger, pop);
  return root;
}

// ---- toggle: the one on/off control; `confirm` (true, or a verb phrase) makes it two-click ----

export function toggle({label, on, confirm, onChange, key, disabled = false, title}) {
  const result = node('button', null, 'toggle');
  result.type = 'button';
  result.setAttribute('role', 'switch');
  result.setAttribute('aria-checked', String(on));
  result.disabled = disabled;
  if (key) result.dataset.key = key;
  if (title) result.title = title;
  const track = node('span', null, 'toggle-track');
  track.append(node('span', null, 'toggle-thumb'));
  const words = node('span', label, 'toggle-label');
  result.append(track, words);
  if (confirm) armed(result, confirmText(typeof confirm === 'string' ? confirm : `turn ${on ? 'off' : 'on'}`), words);
  result.addEventListener('click', async () => {
    result.disabled = true;
    try { await onChange(!on); } finally { result.disabled = disabled; }
  });
  return result;
}

// ---- setting row: label (+ info) on the left, control (+ unit, marker) on the right ----

export function settingRow({label, info: help, control, unit, marker, anchor: id, description}) {
  const row = node('div', null, 'setting');
  const text = node('div', null, 'setting-text');
  const name = node('label');
  const target = control.id ? control : control.querySelector?.('[id]');
  if (target) name.htmlFor = target.id;
  name.append(node('strong', label));
  text.append(name);
  if (help) text.append(info(help));
  if (description) text.append(node('small', description));
  const controls = node('div', null, 'control');
  controls.append(control);
  if (unit) controls.append(node('span', unit, 'muted'));
  if (marker !== undefined && marker !== null) controls.append(marker instanceof Node ? marker : node('span', marker, 'marker'));
  row.append(text, controls);
  return id ? anchor(row, id) : row;
}
