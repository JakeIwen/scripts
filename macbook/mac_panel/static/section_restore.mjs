// llm-restore: the CLI owns its crontab block; the page only flips the snapshot switch.
import {card, info, kv, node, settingRow, tag, timeNode, toggle} from './panel_dom.mjs';
import {action, refresh} from './panel_sections.mjs';
import {stageAction} from './search_actions.mjs';
import {JOBS_COPY} from './section_copy.mjs';
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
  return {node: card({title: 'Session snapshots', level, body, anchor: SNAPSHOTS}), summary, level};
}

export function searchItems(data) {
  return [{
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
