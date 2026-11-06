// User crontab: crontab_sync.py owns the file; pause, resume and install all reinstall the tracked copy.
import {RANGES, rangeToggle, scheduleStrip} from './charts.mjs';
import {anchor, button, card, confirmText, details, emptyState, info, kv, node, tag, timeNode} from './panel_dom.mjs';
import {action} from './panel_sections.mjs';
import {stageAction} from './search_actions.mjs';
import {JOBS_COPY} from './section_copy.mjs';
import {Level, status} from './status_cell.mjs';
import {latestTask, resumeTasks, taskLine, watchTask} from './task_receipt.mjs';

const SECTION = 'crontab';
const RESTORE_BLOCK = 'llm-restore';
const COPY = JOBS_COPY.crontab;
const SECONDS_PER_DAY = 86400;
// A job with more runs than this in the window (every minute, say) would only paint a solid bar.
const MAX_TICKS = 150;
let scheduleRange = '7d';

const jobAnchor = job => `cron-${job.id}`;
const taskJob = job => `crontab:${job.id}`;

function repoNote(data) {
  const count = data.repo_only.length;
  return count ? ` and ${count} repo-only line${count === 1 ? '' : 's'}` : '';
}

function installControl(data) {
  const controls = node('div', null, 'actions');
  controls.append(button('Install macbook/crontab', () => action(SECTION, '/api/crontab',
    {action: 'install', revision: data.revision, confirmed: true}), {
    confirm: confirmText(`install${repoNote(data)}`), key: 'install', disabled: !data.installable,
    title: data.installable ? '' : 'Live-only lines block installation',
  }));
  if (!data.installable) {
    controls.append(node('span', 'Blocked until the live-only lines are added to macbook/crontab or removed from the live crontab.', 'muted small'));
  }
  return controls;
}

function driftBlock(data) {
  const root = anchor(node('div'), 'drift');
  const level = data.in_sync ? Level.OK : data.installable ? Level.WARN : Level.ERROR;
  const line = node('p', null, 'summary-line');
  line.append(status(level, data.in_sync ? 'Live crontab matches macbook/crontab' : 'Live crontab differs from macbook/crontab'));
  root.append(line);
  if (data.uncommitted) root.append(node('p', 'macbook/crontab has uncommitted changes', 'warning'));
  if (data.live_only.length) {
    const live = details(`Live-only lines (${data.live_only.length}) block installation`, data.live_only.join('\n'), 'live-drift');
    live.open = true;
    root.append(live);
  }
  if (data.repo_only.length) root.append(details(`Only in the tracked file (${data.repo_only.length})`, data.repo_only.join('\n'), 'repo-drift'));
  if (!data.in_sync) root.append(installControl(data));
  return {root, level};
}

// ---- schedule timeline ----

function laneNames(jobs) {
  const seen = new Map();
  jobs.forEach(job => seen.set(job.program, (seen.get(job.program) ?? 0) + 1));
  return new Map(jobs.map(job => [job.id, seen.get(job.program) > 1 ? `${job.program} ${job.id.slice(0, 4)}` : job.program]));
}

function scheduleBlock(jobs) {
  const slot = node('div', null, 'schedule-slot');
  const draw = () => {
    const from = Date.now() / 1000;
    const to = from + RANGES[scheduleRange];
    const names = laneNames(jobs);
    const events = [];
    const frequent = [];
    jobs.forEach(job => {
      const runs = job.next_runs.filter(ts => ts <= to);
      if (runs.length > MAX_TICKS) frequent.push(job.program);
      else runs.forEach(ts => events.push({ts, lane: names.get(job.id), label: `${job.program} · ${job.schedule}`}));
    });
    const parts = [];
    if (events.length) parts.push(scheduleStrip({events, from, to, now: from}));
    else parts.push(emptyState(COPY.nothingScheduled));
    if (frequent.length) parts.push(node('p', COPY.frequent + [...new Set(frequent)].join(', '), 'muted small'));
    slot.replaceChildren(...parts);
  };
  draw();
  const head = node('div', null, 'actions');
  head.append(node('strong', 'Next runs'), rangeToggle({
    ranges: Object.keys(RANGES), value: scheduleRange, onChange(next) { scheduleRange = next; draw(); },
  }), info(COPY.scheduleInfo));
  const block = node('div', null, 'chart-block');
  block.append(head, slot);
  return block;
}

// ---- one card per job ----

function runNow(job, data) {
  return button('Run now', () => action(SECTION, '/api/crontab',
    {action: 'run', job: job.id, revision: data.revision, confirmed: true},
    {after: receipt => watchTask(SECTION, receipt, job.program)}),
  {confirm: confirmText(`run ${job.program} now`), key: `run-${job.id}`});
}

function managedNote(job) {
  if (job.managed_block === RESTORE_BLOCK) {
    const link = node('a', `controlled by ${RESTORE_BLOCK}`);
    link.href = `#${RESTORE_BLOCK}`;
    return link;
  }
  return node('span', `use ${job.managed_switch}`, 'muted');
}

function jobActions(job, data) {
  const cell = node('div', null, 'actions');
  cell.append(runNow(job, data), info(COPY.runInfo));
  if (job.managed_switch) {
    cell.append(managedNote(job));
    return cell;
  }
  const kind = job.paused ? 'resume' : 'pause';
  cell.append(button(job.paused ? 'Resume' : 'Pause', () => action(SECTION, '/api/crontab',
    {action: kind, job: job.id, revision: data.revision, confirmed: true}),
  {confirm: confirmText(`${kind} ${job.program}${repoNote(data)}`), key: `${kind}-${job.id}`}));
  return cell;
}

function withInfo(value, text) {
  const wrapper = node('span');
  wrapper.append(value, info(text));
  return wrapper;
}

function jobCard(job, data) {
  const schedule = node('span');
  schedule.append(job.schedule, ' ', node('code', job.expression, 'muted'));
  const lastLog = job.log_path ? node('span') : node('span', 'no log', 'muted');
  if (job.log_path) lastLog.append(timeNode(job.log_modified, 'never written'), ' ', node('span', job.log_path, 'muted small'));
  const next = job.paused ? node('span', 'paused', 'muted')
    : job.next_runs.length ? timeNode(job.next_runs[0]) : node('span', 'none in the next 7 days', 'muted');
  const command = details(node('code', job.program), job.command, `cron-${job.id}`);
  const body = node('div');
  if (job.description) body.append(node('p', job.description));
  body.append(kv([['Schedule', schedule], ['Next run', withInfo(next, COPY.nextInfo)], ['Last log write', withInfo(lastLog, COPY.lastLogInfo)], ['Command', command]]));
  const task = latestTask(data.tasks, taskJob(job));
  if (task) body.append(taskLine(task));
  body.append(jobActions(job, data));
  const title = node('span');
  title.append(job.program, ' ');
  if (job.paused) title.append(tag('paused', Level.WARN));
  if (job.managed_block) title.append(tag(`managed · ${job.managed_block}`));
  return card({title, level: job.paused ? Level.IDLE : Level.OK, body, anchor: jobAnchor(job)});
}

export function render(data) {
  const root = node('div');
  const drift = driftBlock(data);
  root.append(drift.root);
  if (data.jobs.length) root.append(scheduleBlock(data.jobs));
  data.jobs.forEach(job => root.append(jobCard(job, data)));
  if (!data.jobs.length) root.append(emptyState(COPY.empty));
  const labels = new Map(data.jobs.map(job => [taskJob(job), job.program]));
  resumeTasks(SECTION, data.tasks, task => labels.get(task.job) ?? task.job);
  const paused = data.jobs.filter(job => job.paused).length;
  const summary = [data.in_sync ? 'in sync' : `drift (${data.live_only.length} live-only, ${data.repo_only.length} repo-only)`,
    paused && `${paused} paused`].filter(Boolean).join(' · ');
  return {node: root, summary, level: drift.level};
}

export function searchItems(data) {
  return data.jobs.map(job => ({
    title: job.program, subtitle: job.schedule, keywords: ['cron', 'crontab', 'job', job.description, job.expression],
    anchor: jobAnchor(job),
    actions: [{label: 'Run now', run: () => stageAction(SECTION, jobAnchor(job), `run-${job.id}`, {arm: true})}],
  }));
}

export function attentionItems(data) {
  const items = [];
  if (data.live_only.length) {
    items.push({level: Level.ERROR, anchor: 'drift', text: `${data.live_only.length} live-only line${data.live_only.length === 1 ? '' : 's'} block installing macbook/crontab`});
  } else if (!data.in_sync) {
    items.push({level: Level.WARN, anchor: 'drift', text: 'live crontab differs from macbook/crontab'});
  }
  return items;
}
