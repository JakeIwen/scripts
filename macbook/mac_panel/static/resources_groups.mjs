// LLM sessions (with their process trees and the Terminate session button), LLM infrastructure and the
// m4-compute worker and jobs. Only session groups can be signalled; the other two are read-only rows.
import {anchor, card, details, emptyState, info, node, table, tag} from './panel_dom.mjs';
import {RESOURCES_COPY as COPY} from './section_copy.mjs';
import {Level, status} from './status_cell.mjs';
import {sessionAction} from './resources_actions.mjs';
import {numberTable} from './resources_table.mjs';
import {nameUnconfirmed} from './resources_names.mjs';
import {formatAge, formatBytes, formatPercent, plural} from './resources_format.mjs';

export const GroupKind = Object.freeze({CLAUDE: 'claude', CODEX: 'codex', INFRA: 'infra', COMPUTE: 'compute', APP: 'app'});
export const SESSION_KINDS = [GroupKind.CLAUDE, GroupKind.CODEX];
const SESSION_LEVEL = {busy: Level.OK, idle: Level.IDLE};
const TREE_NUMBERS = [1, 2, 3, 4];
const READ_ONLY_NUMBERS = [1, 2, 3, 4];
// The backend names the worker's own row exactly this; every other compute row is a job.
const COMPUTE_WORKER_NAME = 'worker';

export const sessionAnchor = group => `session-${group.id}`;
/** A process row's `group` is a group id, `<kind>:<root pid>`; its kind is the prefix. */
export const groupKind = id => (id ? String(id).split(':')[0] : null);
export const groupsOfKind = (data, kinds) => (data.groups ?? []).filter(group => kinds.includes(group.kind));
const sum = (groups, key) => groups.reduce((total, group) => total + (group[key] ?? 0), 0);

// The choice lives in module state so the 15 s re-render keeps it, as the process list's sort does.
export const SessionSort = Object.freeze({NAME: 'name', CPU: 'cpu', MEMORY: 'rss'});
export const sessionView = {sort: SessionSort.CPU};
const byName = (a, b) => String(a.name).localeCompare(String(b.name));
const SESSION_COMPARE = {
  [SessionSort.NAME]: byName,
  [SessionSort.CPU]: (a, b) => (b.cpu ?? 0) - (a.cpu ?? 0) || byName(a, b),
  [SessionSort.MEMORY]: (a, b) => (b.rss ?? 0) - (a.rss ?? 0) || byName(a, b),
};
// Which header cell (by index into COPY.sessions.heads) sorts by what.
const SORT_HEADS = Object.freeze({0: SessionSort.NAME, 2: SessionSort.CPU, 3: SessionSort.MEMORY});

/** Sessions in the chosen order, each nested session directly under the one it started in (`parent` is the outer group's id), whatever the sort. */
export function orderSessions(groups, sort = sessionView.sort) {
  const outer = group => groups.find(other => other.id === group.parent);
  const compare = SESSION_COMPARE[sort] ?? SESSION_COMPARE[SessionSort.CPU];
  const roots = groups.filter(group => !group.parent || !outer(group)).sort(compare);
  const ordered = [];
  const place = (group, depth) => {
    ordered.push({group, depth});
    groups.filter(child => child.parent && outer(child) === group).sort(compare).forEach(child => place(child, depth + 1));
  };
  roots.forEach(group => place(group, 0));
  return ordered;
}

function indented(content, depth) {
  const cell = node('span', null, 'tree-name');
  cell.style.setProperty('--depth', String(depth));
  cell.append(content instanceof Node ? content : document.createTextNode(String(content)));
  return cell;
}

// Desktop shows a table; on a phone CSS lays each row out as name, CPU and memory on one line and PID and age under it.
export function processTree(group) {
  const rows = (group.processes ?? []).map(proc => {
    const name = node('span');
    name.append(proc.name);
    if (proc.stopped) name.append(' ', tag(COPY.sessions.paused, Level.WARN));
    return [indented(name, proc.depth ?? 0), String(proc.pid), formatPercent(proc.cpu), formatBytes(proc.rss), formatAge(proc.started)];
  });
  const wrapper = numberTable(COPY.sessions.treeHeads, rows, TREE_NUMBERS);
  wrapper.classList.add('tree-table');
  return wrapper;
}

function sessionCells(group) {
  const level = SESSION_LEVEL[group.status] ?? Level.UNKNOWN;
  const name = node('span', null, 'session-name');
  name.append(tag(COPY.kinds[group.kind] ?? group.kind), group.name);
  if (group.detail) name.append(' ', node('span', group.detail, 'muted small'));
  if (nameUnconfirmed(group)) name.append(' ', node('span', COPY.sessions.unconfirmedName, 'muted small'), info(COPY.sessions.info.unconfirmedName));
  const cells = [name, group.status ? status(level, COPY.sessions.status[group.status] ?? group.status) : node('span', '—', 'muted'),
    formatPercent(group.cpu), formatBytes(group.rss), String(group.count ?? '—'), formatAge(group.started)];
  return cells;
}

function sessionRow({group, depth}) {
  const head = node('div', null, 'session-row');
  sessionCells(group).forEach((value, index) => {
    const cell = node('span', null, 'session-cell');
    cell.dataset.label = COPY.sessions.heads[index];
    cell.append(value instanceof Node ? value : document.createTextNode(String(value)));
    head.append(cell);
  });
  const body = node('div', null, 'session-body');
  body.append(sessionAction(group));
  if (group.cwd) body.append(node('p', `${COPY.sessions.cwd}: ${group.cwd}`, 'muted small'));
  body.append(processTree(group));
  const result = details(head, body, `session-${group.id}`);
  anchor(result, sessionAnchor(group));
  result.className = 'session';
  result.style.setProperty('--depth', String(depth));
  return result;
}

function sessionsHead(onSort) {
  const head = node('div', null, 'session-row session-head');
  head.append(node('span', COPY.sessions.sortLabel, 'sort-label'));
  const buttons = [];
  const sync = () => buttons.forEach(({sort, button}) => button.setAttribute('aria-pressed', String(sessionView.sort === sort)));
  COPY.sessions.heads.forEach((text, index) => {
    const sort = SORT_HEADS[index];
    if (!sort) { head.append(node('span', text, 'session-cell')); return; }
    const cell = node('span', null, 'session-cell sort-cell');
    const button = node('button', text, 'sort-head');
    button.type = 'button';
    button.dataset.key = `session-sort-${sort}`;
    button.title = COPY.sessions.sortBy(text);
    // The press updates itself and the rows: a focused control pauses the poll, so no re-render would.
    button.addEventListener('click', () => { sessionView.sort = sort; sync(); onSort(); });
    buttons.push({sort, button});
    cell.append(button);
    head.append(cell);
  });
  sync();
  return head;
}

function sessionList(sessions) {
  const list = node('div', null, 'sessions');
  const rows = () => orderSessions(sessions).map(sessionRow);
  const draw = () => {
    // Rebuilt rows would close every opened session, so reopen the ones that were.
    const open = new Set([...(list.querySelectorAll?.('details[open]') ?? [])].map(item => item.dataset.key));
    const fresh = rows();
    fresh.forEach(item => { if (open.has(item.dataset.key)) item.open = true; });
    list.replaceChildren(list.childNodes[0], ...fresh);
  };
  list.append(sessionsHead(draw), ...rows());
  return list;
}

/** One line per kind: how many sessions and what they hold together. Memory is a sum, so an upper bound. */
export function totalsLine(kind, data) {
  const totals = data.totals?.[kind];
  const sessions = groupsOfKind(data, [kind]).length;
  if (!totals && !sessions) return null;
  const parts = [plural(sessions, 'session')];
  if (totals) parts.push(plural(totals.count ?? 0, 'process', 'processes'), `${formatPercent(totals.cpu)} CPU`, formatBytes(totals.rss));
  return `${COPY.kinds[kind]}: ${parts.join(' · ')}`;
}

// The infra and compute tables share one shape; a blocked reason comes from the root process, else the card's wording.
function readOnlyTable(groups, heads, fallbackReason) {
  const rows = groups.map(group => {
    const root = (group.processes ?? []).find(proc => proc.pid === group.root_pid);
    const name = node('span');
    name.append(group.name);
    if (group.detail) name.append(' ', node('span', group.detail, 'muted small'));
    return [name, formatPercent(group.cpu), formatBytes(group.rss), String(group.count ?? '—'), formatAge(group.started),
      info(root?.blocked ?? fallbackReason)];
  });
  return numberTable(heads, rows, READ_ONLY_NUMBERS);
}

export function sessionsCard(data) {
  const sessions = groupsOfKind(data, SESSION_KINDS);
  const body = node('div');
  if (sessions.length) {
    body.append(sessionList(sessions));
  } else {
    body.append(emptyState(COPY.sessions.empty));
  }
  SESSION_KINDS.map(kind => totalsLine(kind, data)).filter(Boolean).forEach(line => body.append(node('p', line, 'muted small')));
  const infra = groupsOfKind(data, [GroupKind.INFRA]);
  const heading = node('h4', null, 'sub-heading');
  heading.append(COPY.infra.title, info(COPY.infra.info));
  body.append(heading, infra.length ? readOnlyTable(infra, COPY.infra.heads, COPY.infra.readOnly) : emptyState(COPY.infra.empty));
  return card({title: COPY.sessions.title, body, anchor: 'sessions', actions: info(COPY.sessions.info.table)});
}

export const isComputeWorker = group => group.name === COMPUTE_WORKER_NAME;

export function computeCard(data) {
  const groups = groupsOfKind(data, [GroupKind.COMPUTE]);
  const body = node('div');
  if (!groups.length) {
    body.append(emptyState(COPY.compute.notRunning));
  } else {
    const ordered = [...groups.filter(isComputeWorker), ...groups.filter(group => !isComputeWorker(group))];
    body.append(readOnlyTable(ordered, COPY.compute.heads, COPY.compute.readOnly));
    if (!groups.some(group => !isComputeWorker(group))) body.append(node('p', COPY.compute.idle, 'muted'));
  }
  return card({title: COPY.compute.title, body, anchor: 'compute', actions: info(COPY.compute.info)});
}
