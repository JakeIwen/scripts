// Resources: what the Mac is spending, by LLM session, infrastructure, m4-compute job and app.
// Page layout only; each card is built in its own module.
import {node} from './panel_dom.mjs';
import {stageAction} from './search_actions.mjs';
import {RESOURCES_COPY as COPY} from './section_copy.mjs';
import {goTo} from './shell.mjs';
import {Level, status} from './status_cell.mjs';
import {SECTION, processActions, sessionKey} from './resources_actions.mjs';
import {formatBytes, formatPercent, plural} from './resources_format.mjs';
import {SESSION_KINDS, computeCard, groupKind, groupsOfKind, sessionAnchor, sessionsCard} from './resources_groups.mjs';
import {historyCard} from './resources_history.mjs';
import {processesCard, revealProcessList} from './resources_processes.mjs';
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

const pausedRows = data => [...(data.processes ?? []), ...(data.groups ?? []).flatMap(group => group.processes ?? [])]
  .filter(row => row.stopped);

export function attentionItems(data) {
  const level = healthLevel(data.health);
  const items = (data.health?.reasons ?? []).map(text => ({level: level === Level.ERROR ? Level.ERROR : Level.WARN, text, anchor: 'system'}));
  const unavailable = data.health?.unavailable ?? [];
  if (unavailable.length) items.push({level: Level.WARN, text: COPY.attention.probes(unavailable.join(', ')), anchor: 'system'});
  // A process left stopped keeps its memory and does nothing, and nothing else on the page would say so.
  const paused = new Set(pausedRows(data).map(row => row.pid)).size;
  if (paused) items.push({level: Level.WARN, text: `${plural(paused, 'process', 'processes')} ${COPY.attention.paused}`, anchor: 'processes'});
  return items;
}

function sessionItem(group) {
  const anchor = sessionAnchor(group);
  const actions = group.tree_blocked ? [] : [{label: COPY.sessions.terminate, run: () => stageAction(SECTION, anchor, sessionKey(group), {arm: true})}];
  return {title: group.name, subtitle: `${COPY.kinds[group.kind]} session · ${plural(group.count ?? 0, 'process', 'processes')}`,
    keywords: ['session', 'resources', 'process', group.kind], anchor, actions: [{label: 'Open', run: () => goTo(SECTION, anchor)}, ...actions]};
}

// A process button exists only in the By process list, so the search action switches to it before arming.
function processItem(row) {
  const reveal = () => revealProcessList();
  const actions = row.blocked ? [] : processActions(row).map(id => ({
    label: COPY.actions[id].label,
    run: () => { reveal(); stageAction(SECTION, 'processes', `${id}-${row.pid}`, {arm: true}); },
  }));
  return {title: row.name, subtitle: `pid ${row.pid} · ${formatPercent(row.cpu)} CPU · ${formatBytes(row.rss)}`,
    keywords: ['process', 'resources', 'kill', 'terminate', 'memory', 'cpu', groupKind(row.group)].filter(Boolean), anchor: 'processes',
    actions: [{label: 'Open', run: () => { reveal(); goTo(SECTION, 'processes'); }}, ...actions]};
}

export function searchItems(data) {
  return [
    {title: COPY.system.title, subtitle: 'CPU, memory, swap, disk, pressure', keywords: ['resources', 'cpu', 'memory', 'swap', 'disk', 'load', 'thermal', 'battery'],
      anchor: 'system', actions: [{label: 'Open', run: () => goTo(SECTION, 'system')}]},
    ...groupsOfKind(data, SESSION_KINDS).map(sessionItem),
    ...(data.processes ?? []).map(processItem),
  ];
}
