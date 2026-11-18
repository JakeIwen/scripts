// Resources: what the Mac is spending, by LLM session, infrastructure, m4-compute job and app.
// Page layout only; each card is built in its own module.
import {node, notice} from './panel_dom.mjs';
import {stageAction} from './search_actions.mjs';
import {RESOURCES_COPY as COPY} from './section_copy.mjs';
import {goTo} from './shell.mjs';
import {Level, status} from './status_cell.mjs';
import {SECTION, sessionKey} from './resources_actions.mjs';
import {formatBytes, formatPercent, plural} from './resources_format.mjs';
import {SESSION_KINDS, computeCard, groupsOfKind, sessionAnchor, sessionsCard} from './resources_groups.mjs';
import {historyCard} from './resources_history.mjs';
import {processesCard} from './resources_processes.mjs';
import {healthLevel, systemCard} from './resources_system.mjs';

function summaryText(data) {
  const system = data.system ?? {};
  const parts = [];
  if (Number.isFinite(system.cpu_percent)) parts.push(`CPU ${formatPercent(system.cpu_percent)}`);
  if (Number.isFinite(system.memory?.used)) parts.push(`${formatBytes(system.memory.used)} used`);
  const sessions = groupsOfKind(data, SESSION_KINDS).length;
  if (sessions) parts.push(plural(sessions, 'session'));
  return parts.join(' · ') || COPY.summaryUnavailable;
}

export function render(data) {
  const level = healthLevel(data.health);
  const summary = summaryText(data);
  const root = node('div');
  const line = node('p', null, 'summary-line');
  line.append(status(level, summary));
  root.append(line, systemCard(data), sessionsCard(data), computeCard(data), processesCard(data), historyCard());
  return {node: root, summary, level};
}

// Overview attention comes only from the backend's health: a paused process is routine here and shows in the process list.
export function attentionItems(data) {
  const level = healthLevel(data.health);
  const items = (data.health?.reasons ?? []).map(text => ({level: level === Level.ERROR ? Level.ERROR : Level.WARN, text, anchor: 'system'}));
  const unavailable = data.health?.unavailable ?? [];
  if (unavailable.length) items.push({level: Level.WARN, text: COPY.attention.probes(unavailable.join(', ')), anchor: 'system'});
  return items;
}

function sessionItem(group) {
  const anchor = sessionAnchor(group);
  const stage = () => stageAction(SECTION, anchor, sessionKey(group),
    {arm: true, onMissing: () => notice(SECTION, COPY.sessions.staleTarget, 'error')});
  const actions = group.tree_blocked ? [] : [{label: COPY.sessions.terminate, run: stage}];
  return {title: group.name, subtitle: `${COPY.kinds[group.kind]} session · ${plural(group.count ?? 0, 'process', 'processes')}`,
    keywords: ['session', 'resources', 'process', group.kind], anchor, actions: [{label: 'Open', run: () => goTo(SECTION, anchor)}, ...actions]};
}

// Cards the page draws whole, so opening one always lands on something rendered. Individual processes are not indexed:
// the list's own filter finds them, and a result beyond its first rows would have no row to reveal.
const cardItem = (title, subtitle, anchor, keywords) => ({title, subtitle, keywords: ['resources', ...keywords], anchor,
  actions: [{label: 'Open', run: () => goTo(SECTION, anchor)}]});

export function searchItems(data) {
  return [
    cardItem(COPY.system.title, 'CPU, memory, swap, disk, pressure', 'system', ['cpu', 'memory', 'swap', 'disk', 'load', 'thermal', 'battery']),
    cardItem(COPY.sessions.title, 'Claude and Codex sessions, their processes and infrastructure', 'sessions', ['claude', 'codex', 'terminate', 'infrastructure']),
    ...groupsOfKind(data, SESSION_KINDS).map(sessionItem),
    cardItem(COPY.compute.title, 'van-compute worker and jobs', 'compute', ['van-compute', 'worker', 'job']),
    cardItem(COPY.processes.title, 'every process: filter, sort, pause, terminate', 'processes', ['process', 'kill', 'terminate', 'pause', 'memory', 'cpu', 'app']),
    cardItem(COPY.history.title, 'CPU and memory over time', 'history', ['history', 'chart', 'cpu', 'memory']),
  ];
}
