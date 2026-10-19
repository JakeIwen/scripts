// llm-restore: the CLI owns its crontab block; the page only flips the snapshot switch.
import {button, keyValues, node, timeNode} from './panel_dom.mjs';
import {action, refresh} from './panel_sections.mjs';
import {Level, status, tag} from './status_cell.mjs';

const SECTION = 'llm-restore';

export function render(data) {
  const root = node('div');
  const line = node('p', null, 'summary-line');
  line.append(status(data.enabled ? Level.OK : Level.IDLE, data.enabled ? 'Snapshots on' : 'Snapshots off'), ' ');
  line.append(tag(data.installed ? 'cron block installed' : 'cron block missing', data.installed ? undefined : Level.ERROR));
  line.append(tag(data.up_to_date ? 'up to date' : 'cron block needs update', data.up_to_date ? undefined : Level.WARN));
  root.append(line);
  root.append(keyValues([['Last snapshot', timeNode(data.last_snapshot)], ['Open sessions', data.sessions_open ?? 'unknown']]));
  root.append(button(data.enabled ? 'Turn off' : 'Turn on', () => action(SECTION, '/api/llm-restore',
    {enabled: !data.enabled}, {after: () => refresh('crontab')}), {key: 'toggle'}));
  root.append(node('p', 'The every-minute snapshot records the open Claude Code and Codex sessions that llmrestore reopens after a reboot.', 'muted small'));
  const level = !data.installed ? Level.ERROR : !data.up_to_date ? Level.WARN : data.enabled ? Level.OK : Level.IDLE;
  const summary = `${data.enabled ? 'on' : 'off'} · ${data.sessions_open ?? '?'} sessions`;
  return {node: root, summary, level};
}
