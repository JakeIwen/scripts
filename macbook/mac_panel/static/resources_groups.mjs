// LLM sessions (with their process trees and the Terminate session button), LLM infrastructure and the
// m4-compute worker and jobs. Only session groups can be signalled; the other two are read-only rows.
import {anchor, card, details, emptyState, info, node, table, tag} from './panel_dom.mjs';
import {RESOURCES_COPY as COPY} from './section_copy.mjs';
import {Level, status} from './status_cell.mjs';
import {sessionAction} from './resources_actions.mjs';
import {numberTable} from './resources_table.mjs';
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

/** Sessions by name, each nested session directly under the one it started in (`parent` is the outer group's id). */
export function orderSessions(groups) {
  const outer = group => groups.find(other => other.id === group.parent);
  const byName = (a, b) => String(a.name).localeCompare(String(b.name));
  const roots = groups.filter(group => !group.parent || !outer(group)).sort(byName);
  const ordered = [];
  const place = (group, depth) => {
    ordered.push({group, depth});
    groups.filter(child => child.parent && outer(child) === group).sort(byName).forEach(child => place(child, depth + 1));
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

function processTree(group) {
  const rows = (group.processes ?? []).map(proc => {
    const name = node('span');
    name.append(proc.name);
    if (proc.stopped) name.append(' ', tag(COPY.sessions.paused, Level.WARN));
    return [indented(name, proc.depth ?? 0), String(proc.pid), formatPercent(proc.cpu), formatBytes(proc.rss), formatAge(proc.started)];
  });
  return numberTable(COPY.sessions.treeHeads, rows, TREE_NUMBERS);
}

function sessionCells(group) {
  const level = SESSION_LEVEL[group.status] ?? Level.UNKNOWN;
  const name = node('span', null, 'session-name');
  name.append(tag(COPY.kinds[group.kind] ?? group.kind), group.name);
  if (group.detail) name.append(' ', node('span', group.detail, 'muted small'));
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

function sessionsHead() {
  const head = node('div', null, 'session-row session-head');
  COPY.sessions.heads.forEach(text => head.append(node('span', text, 'session-cell')));
  return head;
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
    const list = node('div', null, 'sessions');
    list.append(sessionsHead(), ...orderSessions(sessions).map(sessionRow));
    body.append(list);
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
