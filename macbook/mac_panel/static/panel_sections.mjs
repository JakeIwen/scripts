// Section registry plus the two verbs every section uses: refresh (GET) and action (POST then refresh).
import {api} from './panel_api.mjs';
import {button, focusKey, isChanged, node, notice, restoreFocus, timeNode} from './panel_dom.mjs';
import {Level} from './status_cell.mjs';

const registry = new Map();
const busy = new Set();
const failedLoads = new Set();

export function register(list) {
  list.forEach(entry => registry.set(entry.id, entry));
}

export function sections() {
  return [...registry.values()];
}

function sectionPart(id, selector) {
  return document.querySelector(`#${CSS.escape(id)} ${selector}`);
}

// Why a poll must wait: anything the next render would silently throw away.
export function editingReason(id) {
  const root = document.getElementById(id);
  if (!root) return null;
  if (root.querySelector('button.armed')) return 'a confirmation is pending';
  if (root.querySelector('[data-editing]')) return 'an edit is open';
  if ([...root.querySelectorAll('input, select, textarea')].some(isChanged)) return 'unsaved edits';
  const active = document.activeElement;
  if (root.contains(active) && ['INPUT', 'SELECT', 'TEXTAREA'].includes(active.tagName)) return 'a field has focus';
  return null;
}

function setSummary(id, summary, level) {
  const link = document.querySelector(`nav a[href="#${CSS.escape(id)}"]`);
  if (!link) return;
  link.querySelector('.dot').className = `dot level-${level}`;
  link.querySelector('.summary').textContent = summary ? ` · ${summary}` : '';
}

function setMeta(id, className, ...parts) {
  const meta = sectionPart(id, '.meta');
  if (!meta) return;
  meta.className = className ? `meta ${className}` : 'meta';
  meta.replaceChildren(...parts);
}

export function markPaused(id, reason) {
  setMeta(id, 'paused', `auto-refresh paused: ${reason}`);
}

function markUpdated(id) {
  const entry = registry.get(id);
  setMeta(id, '', 'updated ', timeNode(Date.now() / 1000), entry?.auto ? ' · auto' : '');
}

export async function refresh(id) {
  if (busy.has(id)) return;
  busy.add(id);
  const entry = registry.get(id);
  const content = sectionPart(id, '.content');
  try {
    const value = await api(`/api/${id}`);
    // Disclosures keep the state the reader gave them; ones new to this render keep the renderer's default.
    const open = new Map([...content.querySelectorAll('details')].map(x => [x.dataset.key, x.open]));
    const focus = focusKey(content);
    const result = entry.render(value);
    content.replaceChildren(result.node);
    content.querySelectorAll('details').forEach(x => { if (open.has(x.dataset.key)) x.open = open.get(x.dataset.key); });
    restoreFocus(content, focus);
    setSummary(id, result.summary, result.level);
    markUpdated(id);
    if (failedLoads.delete(id)) notice(id, '');
  } catch (error) {
    failedLoads.add(id);
    notice(id, error.message, 'error');
    setSummary(id, 'unavailable', Level.ERROR);
    if (content.querySelector('.loading, .load-failed')) {
      const failed = node('p', 'Could not load this section. ', 'load-failed');
      failed.append(button('Retry', () => refresh(id), {key: 'retry'}));
      content.replaceChildren(failed);
    }
  } finally {
    busy.delete(id);
  }
}

export async function action(id, path, body, {after, onError, reloadOnError = true} = {}) {
  if (busy.has(id)) {
    notice(id, 'Section is busy; try again shortly', 'error');
    return undefined;
  }
  busy.add(id);
  notice(id, 'Working…', 'working');
  let completed = false;
  try {
    const result = await api(path, body);
    notice(id, result.message || 'Completed', 'ok');
    completed = true;
    if (after) await after(result);
    return result;
  } catch (error) {
    notice(id, error.message, 'error');
    if (onError) onError(error);
    return undefined;
  } finally {
    busy.delete(id);
    if (completed || reloadOnError) await refresh(id);
  }
}
