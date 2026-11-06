// Section registry, the state registry that Overview and search read, and the two verbs every
// section uses: refresh (GET) and action (POST then refresh).
import {api} from './panel_api.mjs';
import {focusKey, isChanged, loadError, notice, restoreFocus, timeNode} from './panel_dom.mjs';
import {Level} from './status_cell.mjs';

const registry = new Map();
const busy = new Set();
const failedLoads = new Set();
const loadedAt = new Map();
const pendingRefreshes = new Set();

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

// state[section] = {payload, summary, level, updatedAt, error}; written on every refresh, read by the
// nav dots, Overview and search. Subscribers run synchronously after each write.
export const state = {};
const listeners = new Set();

export function onState(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function record(id, patch) {
  state[id] = {payload: null, summary: '', level: Level.UNKNOWN, updatedAt: null, error: null, ...state[id], ...patch};
  listeners.forEach(listener => listener(id, state[id]));
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

// Polls and task completions share the same edit guard, including edits made during a GET.
function pauseForEditing(id) {
  const reason = editingReason(id);
  if (!reason) return false;
  markPaused(id, reason);
  pendingRefreshes.add(id);
  return true;
}

export const refreshPending = id => pendingRefreshes.has(id);
export const refreshIfIdle = id => refresh(id, {whenIdle: true});

// Milliseconds since the section last loaded, so a tab opened later can tell whether it is stale.
export function loadedAge(id) {
  return loadedAt.has(id) ? Date.now() - loadedAt.get(id) : Infinity;
}

function markUpdated(id) {
  const entry = registry.get(id);
  loadedAt.set(id, Date.now());
  setMeta(id, '', 'updated ', timeNode(Date.now() / 1000), entry?.auto ? ' · auto' : '');
}

// An entry with `endpoint: null` has nothing to fetch and renders from the state registry alone;
// one with `load()` fetches its payload itself (a section drawing from more than one endpoint).
const load = entry => (entry.load ? entry.load() : entry.endpoint === null ? null : api(entry.endpoint ?? `/api/${entry.id}`));

export async function refresh(id, {whenIdle = false} = {}) {
  if (whenIdle && pauseForEditing(id)) return;
  if (busy.has(id)) return;
  busy.add(id);
  const entry = registry.get(id);
  const content = sectionPart(id, '.content');
  try {
    const value = await load(entry);
    if (whenIdle && pauseForEditing(id)) return;
    // Disclosures keep the state the reader gave them; ones new to this render keep the renderer's default.
    const open = new Map([...content.querySelectorAll('details')].map(x => [x.dataset.key, x.open]));
    const focus = focusKey(content);
    const result = entry.render(value);
    content.replaceChildren(result.node);
    content.querySelectorAll('details').forEach(x => { if (open.has(x.dataset.key)) x.open = open.get(x.dataset.key); });
    restoreFocus(content, focus);
    pendingRefreshes.delete(id);
    markUpdated(id);
    record(id, {payload: value, summary: result.summary, level: result.level, updatedAt: Date.now(), error: null});
    if (failedLoads.delete(id)) notice(id, '');
  } catch (error) {
    failedLoads.add(id);
    notice(id, error.message, 'error');
    record(id, {summary: 'unavailable', level: Level.ERROR, error: error.message});
    if (content.querySelector('.loading, .load-error')) {
      content.replaceChildren(loadError('Could not load this section.', () => refresh(id)));
    }
  } finally {
    busy.delete(id);
  }
}

export async function action(id, path, body, {after, onError, reloadOnError = true, whenIdle = false} = {}) {
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
    // whenIdle defers the reload while the section holds unsaved edits (the next idle poll picks it up).
    if (completed || reloadOnError) await refresh(id, {whenIdle: completed && whenIdle});
  }
}
