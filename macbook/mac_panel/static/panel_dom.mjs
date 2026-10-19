// DOM helpers every section shares: elements, tables, armed buttons, notices, times and focus keys.
import {absoluteText, parseStamp, relativeText} from './relative_time.mjs';

export const PLACEHOLDER = '—';
const ARM_SECONDS = 8;
const OK_NOTICE_SECONDS = 20;

export function node(tag, text, className) {
  const result = document.createElement(tag);
  if (text !== undefined && text !== null) result.textContent = String(text);
  if (className) result.className = className;
  return result;
}

export function timeNode(value, placeholder = PLACEHOLDER) {
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

export function details(title, body, key) {
  const result = node('details');
  if (key) result.dataset.key = key;
  const summary = node('summary');
  summary.append(title instanceof Node ? title : document.createTextNode(String(title)));
  if (key) summary.dataset.key = `summary-${key}`;
  result.append(summary, body instanceof Node ? body : node('pre', body));
  return result;
}

export function keyValues(pairs) {
  const list = node('dl', null, 'kv');
  pairs.forEach(([term, value]) => {
    if (value === undefined) return;
    const definition = node('dd');
    definition.append(value instanceof Node ? value : document.createTextNode(String(value ?? PLACEHOLDER)));
    list.append(node('dt', term), definition);
  });
  return list;
}

export function table(headers, rows) {
  const wrapper = node('div', null, 'table-wrap');
  const result = node('table');
  const head = node('tr');
  headers.forEach(text => head.append(node('th', text)));
  const thead = node('thead');
  thead.append(head);
  result.append(thead);
  const body = node('tbody');
  rows.forEach(cells => {
    const row = node('tr');
    cells.forEach((value, index) => {
      const cell = node('td');
      // Narrow layouts stack cells and show this label in place of the column header.
      cell.dataset.label = headers[index];
      if (!headers[index]) cell.classList.add('full');
      cell.append(value instanceof Node ? value : document.createTextNode(String(value ?? PLACEHOLDER)));
      row.append(cell);
    });
    body.append(row);
  });
  result.append(body);
  wrapper.append(result);
  return wrapper;
}

export function button(text, onClick, options = {}) {
  const {confirm, key, className, disabled = false, title} = options;
  const result = node('button', text, className);
  result.type = 'button';
  if (key) result.dataset.key = key;
  if (title) result.title = title;
  result.disabled = disabled;
  let armed = false;
  let timer = null;
  const disarm = () => {
    if (!armed) return;
    armed = false;
    clearTimeout(timer);
    result.textContent = text;
    result.classList.remove('armed');
  };
  result.addEventListener('click', async () => {
    if (confirm && !armed) {
      armed = true;
      // CSSOM sizing is not an inline style attribute, so the CSP's style-src stays intact.
      result.style.minWidth = `${result.offsetWidth}px`;
      result.textContent = confirm;
      result.classList.add('armed');
      timer = setTimeout(disarm, ARM_SECONDS * 1000);
      return;
    }
    disarm();
    result.disabled = true;
    try { await onClick(); } finally { result.disabled = disabled; }
  });
  result.addEventListener('keydown', event => { if (event.key === 'Escape') disarm(); });
  result.addEventListener('blur', disarm);
  return result;
}

const noticeTimers = new Map();

export function notice(section, text, kind = '') {
  const target = document.querySelector(`#${CSS.escape(section)} .notice`);
  if (!target) return;
  clearTimeout(noticeTimers.get(section));
  target.className = kind ? `notice ${kind}` : 'notice';
  target.replaceChildren();
  if (!text) return;
  target.append(document.createTextNode(text));
  if (kind === 'ok' || kind === 'error') target.append(document.createTextNode(' · '), timeNode(Date.now() / 1000));
  if (kind === 'ok') noticeTimers.set(section, setTimeout(() => notice(section, ''), OK_NOTICE_SECONDS * 1000));
}

// A control differs from what the last render put in it; selects have no defaultValue of their own.
export function isChanged(element) {
  if (element.tagName === 'SELECT') return [...element.options].some(option => option.selected !== option.defaultSelected);
  if (element.type === 'checkbox') return element.checked !== element.defaultChecked;
  return element.value !== element.defaultValue;
}

export function focusKey(root) {
  const active = document.activeElement;
  return root.contains(active) ? active.dataset.key || null : null;
}

export function restoreFocus(root, key) {
  if (!key) return;
  root.querySelector(`[data-key="${CSS.escape(key)}"]`)?.focus({preventScroll: true});
}
