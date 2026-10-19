// User crontab: crontab_sync.py owns the file; pause, resume and install all reinstall the tracked copy.
import {button, details, node, table, timeNode} from './panel_dom.mjs';
import {action} from './panel_sections.mjs';
import {Level, status, tag} from './status_cell.mjs';

const SECTION = 'crontab';
const RESTORE_BLOCK = 'llm-restore';

function repoNote(data) {
  const count = data.repo_only.length;
  return count ? ` · also installs ${count} repo-only line${count === 1 ? '' : 's'}` : '';
}

function installControl(data) {
  const controls = node('div', null, 'actions');
  controls.append(button('Install macbook/crontab', () => action(SECTION, '/api/crontab',
    {action: 'install', revision: data.revision, confirmed: true}), {
    confirm: `Confirm install${repoNote(data)}`, key: 'install', disabled: !data.installable,
    title: data.installable ? '' : 'Live-only lines block installation',
  }));
  if (!data.installable) {
    controls.append(node('span', 'Blocked until the live-only lines are added to macbook/crontab or removed from the live crontab.', 'muted small'));
  }
  return controls;
}

function driftBlock(data) {
  const root = node('div');
  const level = data.in_sync ? Level.OK : data.installable ? Level.WARN : Level.ERROR;
  const line = node('p', null, 'summary-line');
  line.append(status(level, data.in_sync ? 'Live crontab matches macbook/crontab' : 'Live crontab differs from macbook/crontab'));
  root.append(line);
  if (data.uncommitted) root.append(node('p', 'macbook/crontab has uncommitted changes', 'warning'));
  if (data.order_changed) root.append(node('p', 'The same lines appear in a different order.', 'muted'));
  if (data.live_only.length) {
    const live = details(`Live-only lines (${data.live_only.length}) block installation`, data.live_only.join('\n'), 'live-drift');
    live.open = true;
    root.append(live);
  }
  if (data.repo_only.length) root.append(details(`Only in the tracked file (${data.repo_only.length})`, data.repo_only.join('\n'), 'repo-drift'));
  if (!data.in_sync) root.append(installControl(data));
  return {root, level};
}

function jobAction(job, data) {
  const cell = node('div', null, 'actions');
  if (job.managed_switch) {
    if (job.managed_block === RESTORE_BLOCK) {
      const link = node('a', `controlled by ${RESTORE_BLOCK}`);
      link.href = `#${RESTORE_BLOCK}`;
      cell.append(link);
    } else {
      cell.append(node('span', `use ${job.managed_switch}`, 'muted'));
    }
    return cell;
  }
  const kind = job.paused ? 'resume' : 'pause';
  cell.append(button(job.paused ? 'Resume' : 'Pause', () => action(SECTION, '/api/crontab',
    {action: kind, job: job.id, revision: data.revision}), {confirm: `Confirm ${kind}${repoNote(data)}`, key: `${kind}-${job.id}`}));
  return cell;
}

function jobRow(job, data) {
  const schedule = node('div');
  schedule.append(node('div', job.schedule), node('div', job.expression, 'muted small'));
  const tags = node('div');
  if (job.paused) tags.append(tag('paused', Level.WARN));
  if (job.managed_block) tags.append(tag(`managed · ${job.managed_block}`));
  schedule.append(tags);
  const command = node('div');
  if (job.description) command.append(node('p', job.description));
  command.append(details(node('code', job.program), job.command, `cron-${job.id}`));
  const lastRun = node('div');
  if (job.log_path) lastRun.append(timeNode(job.log_modified, 'never written'), node('div', job.log_path, 'muted small'));
  else lastRun.append(node('span', 'no log', 'muted'));
  return [schedule, command, lastRun, jobAction(job, data)];
}

export function render(data) {
  const root = node('div');
  const drift = driftBlock(data);
  root.append(drift.root);
  const rows = data.jobs.map(job => jobRow(job, data));
  root.append(rows.length ? table(['Schedule', 'Command', 'Last log write', 'Action'], rows) : node('p', 'No cron jobs'));
  const paused = data.jobs.filter(job => job.paused).length;
  const summary = [data.in_sync ? 'in sync' : `drift (${data.live_only.length} live-only, ${data.repo_only.length} repo-only)`,
    paused && `${paused} paused`].filter(Boolean).join(' · ');
  return {node: root, summary, level: drift.level};
}
