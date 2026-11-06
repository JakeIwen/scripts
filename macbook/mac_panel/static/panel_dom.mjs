// DOM helpers every section shares: tables, disclosures, notices and focus keys, plus the lower layers re-exported.
import {timeNode} from './components.mjs';
import {PLACEHOLDER, node} from './dom_core.mjs';

export {PLACEHOLDER, armed, button, confirmText, node} from './dom_core.mjs';
export {anchor, card, emptyState, fieldError, info, kv, kv as keyValues, loadError, refreshTimes, settingRow,
  tag, timeNode, timeWithAbsolute, toggle} from './components.mjs';

const OK_NOTICE_SECONDS = 20;

export function details(title, body, key) {
  const result = node('details');
  if (key) result.dataset.key = key;
  const summary = node('summary');
  summary.append(title instanceof Node ? title : document.createTextNode(String(title)));
  if (key) summary.dataset.key = `summary-${key}`;
  result.append(summary, body instanceof Node ? body : node('pre', body));
  return result;
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
  if (element.type === 'checkbox' || element.type === 'radio') return element.checked !== element.defaultChecked;
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
