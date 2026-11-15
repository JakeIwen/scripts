// llm-restore: the CLI owns its crontab block and saved sets; the page flips the snapshot switch and relays
// restore/save (restore_snapshots.mjs).
import {card, info, kv, node, settingRow, tag, timeNode, toggle} from './panel_dom.mjs';
import {action, refresh} from './panel_sections.mjs';
import {stageAction} from './search_actions.mjs';
import {JOBS_COPY} from './section_copy.mjs';
import {AUTO_KEY, NAME_KEY, SNAPSHOT_ANCHOR, snapshotCard} from './restore_snapshots.mjs';
import {Level, status} from './status_cell.mjs';

const SECTION = 'llm-restore';
const COPY = JOBS_COPY.restore;
const SNAPSHOTS = 'snapshots';

const setSnapshots = (enabled, after) => action(SECTION, '/api/llm-restore', {enabled}, {after});

const restoreLevel = data => !data.installed ? Level.ERROR : !data.up_to_date ? Level.WARN : data.enabled ? Level.OK : Level.IDLE;

export function render(data) {
  const line = node('p', null, 'summary-line');
  line.append(status(data.enabled ? Level.OK : Level.IDLE, data.enabled ? 'Snapshots on' : 'Snapshots off'), ' ');
  line.append(tag(data.installed ? 'cron block installed' : 'cron block missing', data.installed ? undefined : Level.ERROR));
  line.append(tag(data.up_to_date ? 'up to date' : 'cron block needs update', data.up_to_date ? undefined : Level.WARN));
  const sessions = node('span');
  sessions.append(String(data.sessions_open ?? 'unknown'), info(COPY.sessionsInfo));
  const last = node('span');
  last.append(timeNode(data.last_snapshot), info(COPY.lastInfo));
  const body = node('div');
  body.append(line, settingRow({
    label: 'Snapshots', info: COPY.toggleInfo,
    control: toggle({label: data.enabled ? 'On' : 'Off', on: data.enabled, key: 'toggle', onChange: on => setSnapshots(on, () => refresh('crontab'))}),
  }), kv([['Last snapshot', last], ['Open sessions', sessions]]));
  const level = restoreLevel(data);
  const summary = `${data.enabled ? 'on' : 'off'} · ${data.sessions_open ?? '?'} sessions`;
  const root = node('div');
  root.append(card({title: 'Session snapshots', level, body, anchor: SNAPSHOTS}), snapshotCard(data));
  return {node: root, summary, level};
}

export function searchItems(data) {
  const sets = data.saved.map(set => set.name);
  return [{
    title: 'Restore a session snapshot', subtitle: `${sets.length} saved sets`, keywords: ['llmrestore', 'reopen', 'restore', 'snapshot', 'reboot', 'saved', ...sets],
    anchor: SNAPSHOT_ANCHOR,
    actions: [{label: 'Choose snapshot', run: () => stageAction(SECTION, SNAPSHOT_ANCHOR, AUTO_KEY)}],
  }, {
    title: 'Take a session snapshot', subtitle: 'save the open sessions as a named set', keywords: ['llmrestore', 'save', 'snapshot', 'take', 'sessions'],
    anchor: SNAPSHOT_ANCHOR,
    actions: [{label: 'Name snapshot', run: () => stageAction(SECTION, SNAPSHOT_ANCHOR, NAME_KEY)}],
  }, {
    title: 'llm-restore snapshots', subtitle: data.enabled ? 'on' : 'off', keywords: ['llmrestore', 'sessions', 'claude', 'codex', 'snapshot'],
    anchor: SNAPSHOTS,
    actions: [{label: data.enabled ? 'Turn off' : 'Turn on', run: () => stageAction(SECTION, SNAPSHOTS, 'toggle')}],
  }];
}

export function attentionItems(data) {
  if (!data.installed) return [{level: Level.ERROR, text: 'the cron block is missing', anchor: SNAPSHOTS}];
  if (!data.up_to_date) return [{level: Level.WARN, text: 'the cron block needs an update', anchor: SNAPSHOTS}];
  return [];
}
