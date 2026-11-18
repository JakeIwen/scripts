// Deferred jobs: the deferred.zsh CLI owns the jobs; the page shows them and relays explicit commands.
import {RANGES, rangeToggle, scheduleStrip} from './charts.mjs';
import {button, card, confirmText, details, emptyState, info, kv, node, timeNode, timeWithAbsolute}
  from './panel_dom.mjs';
import {action, refresh} from './panel_sections.mjs';
import {stageAction} from './search_actions.mjs';
import {JOBS_COPY} from './section_copy.mjs';
import {addJobForm} from './section_deferred_add.mjs';
import {addAgentForm} from './section_deferred_agent.mjs';
import {LAUNCH_UNKNOWN, agentTags, attemptOf, cardOutcome, describeRunNow, relaunchConfirm,
  relaunchFields, remoteRows, rescheduleLocked} from './deferred_agent_job.mjs';
import {readyDetails, stateLabel} from './section_deferred_ready.mjs';
import {Level, status, worst} from './status_cell.mjs';
import {TaskState, latestTask, taskLine, watchTask} from './task_receipt.mjs';
import {parseStamp} from './relative_time.mjs';

const SECTION = 'deferred';
const COPY = JOBS_COPY.deferred;
const jobAnchor = job => `deferred-${job.id}`;
const dueSeconds = job => parseStamp(job.due) / 1000;
let scheduleRange = '7d';
// Job id -> the Pi attempt its card showed when the CLI refused its cancel (a run may be active there). Only
// that attempt offers Abandon, and the request names it, so the CLI keeps the job once another has replaced it.
const abandonable = new Map();

function apply(job, kind, extra = {}, options = {}) {
  return action(SECTION, '/api/deferred', {action: kind, id: job.id, ...extra}, options);
}

function checkerLine(checker) {
  const level = !checker.agent_loaded ? Level.ERROR : checker.stale ? Level.WARN : Level.OK;
  const detail = node('span');
  detail.append(checker.stale ? 'stale · ' : '', 'last run ', timeNode(checker.last_run));
  const line = node('p', null, 'summary-line');
  line.append(status(level, checker.agent_loaded ? 'Checker agent loaded' : 'Checker agent NOT loaded', detail));
  return {line, level};
}

function jobDetails(job) {
  const body = node('div');
  body.append(kv([['Created', timeWithAbsolute(job.created)], ['Origin', job.origin],
    ['Timeout', `${job.timeout} s`], ['Attempts', `${job.attempts}/${job.max_attempts}`], ...remoteRows(job), ['Id', job.id]]));
  if (job.last_log) body.append(node('div', 'Last log', 'muted small'), node('pre', job.last_log));
  return details('Details', body, `job-${job.id}`);
}

function rescheduleEditor(job, cell) {
  cell.dataset.editing = 'true';
  cell.replaceChildren();
  const input = node('input');
  input.type = 'datetime-local';
  input.value = job.due;
  input.defaultValue = job.due;
  input.setAttribute('aria-label', `New due time for ${job.id}`);
  input.dataset.key = `due-${job.id}`;
  const confirm = button('Apply', () => apply(job, 'reschedule', {due: input.value.replace('T', ' ')}),
    {key: `apply-${job.id}`, disabled: true});
  input.addEventListener('input', () => { confirm.disabled = !input.value || input.value === job.due; });
  cell.append(input, confirm, button('Discard', () => refresh(SECTION), {key: `discard-${job.id}`}));
  input.focus();
}

function cancelButtons(job) {
  const attempt = attemptOf(job);
  const refused = error => { if (error.code === 'remote-active' && attempt) abandonable.set(job.id, attempt); };
  const cancel = button('Cancel job', () => apply(job, 'cancel', {confirmed: true}, {onError: refused}),
    {confirm: confirmText(`cancel ${job.name}`), key: `cancel-${job.id}`});
  if (!attempt || abandonable.get(job.id) !== attempt) return [cancel];
  const abandon = button('Abandon job', () => apply(job, 'cancel', {confirmed: true, abandon: true, attempt}),
    {confirm: confirmText(COPY.agent.abandon(attempt)), key: `abandon-${job.id}`});
  return [cancel, abandon, info(COPY.agent.abandonInfo)];
}

function jobActions(job) {
  const cell = node('div', null, 'actions');
  // An unresolved Pi attempt sends the id this page showed; a 409 (the state moved on) shows its message and
  // reloads, which is apply's default, so the controls match the fresh state.
  cell.append(button('Run now', () => apply(job, 'run-now', {confirmed: true, ...relaunchFields(job)},
    {after: receipt => watchTask(SECTION, receipt, job.name, describeRunNow)}),
    {confirm: relaunchConfirm(job, `run ${job.name} now`), key: `run-${job.id}`}));
  if (job.state === 'failed') {
    // Only an unresolved Pi attempt arms Retry: it may start another Pi run beside it.
    cell.append(button('Retry', () => apply(job, 'retry', relaunchFields(job)),
      {confirm: relaunchConfirm(job), key: `retry-${job.id}`}));
  }
  if (rescheduleLocked(job)) cell.append(info(COPY.agent.rescheduleLocked));
  else cell.append(button('Reschedule…', () => rescheduleEditor(job, cell), {key: `reschedule-${job.id}`}));
  cell.append(...cancelButtons(job));
  return cell;
}

function jobCard(job, tasks) {
  if (!job.valid) {
    const body = status(Level.ERROR, 'invalid job', job.problem ? `${job.problem}; inspect in Terminal` : 'inspect in Terminal');
    return card({title: job.id, level: Level.ERROR, body, anchor: jobAnchor(job)});
  }
  const failed = job.state === 'failed';
  const body = node('div');
  body.append(status(failed ? Level.ERROR : Level.IDLE, stateLabel(job), `${job.attempts}/${job.max_attempts} attempts`));
  const tags = agentTags(job);
  if (tags) body.append(tags);
  if (job.description) body.append(node('p', job.description));
  body.append(kv([['Due', timeWithAbsolute(job.due)]]));
  const task = latestTask(tasks, job.id);
  if (task) body.append(taskLine(task, cardOutcome(job, task)));
  body.append(readyDetails(job), jobDetails(job), jobActions(job));
  return card({title: job.name, level: failed ? Level.ERROR : Level.IDLE, body, anchor: jobAnchor(job)});
}

function orphanTasks(tasks) {
  const body = node('div');
  tasks.forEach(task => {
    body.append(node('div', `${task.job}:`), taskLine(task));
  });
  return details(`Panel runs of jobs no longer listed (${tasks.length})`, body, 'orphan-tasks');
}

function scheduleBlock(jobs) {
  const slot = node('div', null, 'schedule-slot');
  const draw = () => {
    const now = Date.now() / 1000;
    const events = jobs.map(job => ({ts: dueSeconds(job), lane: job.name, label: job.name}));
    // Overdue jobs start the window at their due time so they stay on the strip instead of vanishing.
    const from = Math.min(now, ...events.map(event => event.ts)) - 60;
    const to = now + RANGES[scheduleRange];
    slot.replaceChildren(events.some(event => event.ts <= to) ? scheduleStrip({events, from, to, now}) : emptyState(COPY.scheduleEmpty));
  };
  draw();
  const head = node('div', null, 'actions');
  head.append(node('strong', 'Due times'), rangeToggle({
    ranges: Object.keys(RANGES), value: scheduleRange, onChange(next) { scheduleRange = next; draw(); },
  }), info(COPY.scheduleInfo));
  const block = node('div', null, 'chart-block');
  block.append(head, slot);
  return block;
}

export function render(data) {
  const root = node('div');
  const checker = checkerLine(data.checker);
  root.append(checker.line, addJobForm(), addAgentForm());
  [...abandonable.keys()].forEach(id => { if (!data.jobs.some(job => job.id === id)) abandonable.delete(id); });
  const valid = data.jobs.filter(job => job.valid);
  if (valid.length) root.append(scheduleBlock(valid));
  if (!data.jobs.length) root.append(emptyState(COPY.empty));
  data.jobs.forEach(job => root.append(jobCard(job, data.tasks)));
  const orphans = data.tasks.filter(task => !data.jobs.some(job => job.id === task.job));
  if (orphans.length) root.append(orphanTasks(orphans));
  root.append(node('p', `Jobs directory: ${data.jobs_dir}`, 'muted small'));
  const names = new Map(data.jobs.map(job => [job.id, job.name ?? job.id]));
  // Not resumeTasks: a resumed run-now reports with the same Pi-aware wording as one watched from the click.
  data.tasks.filter(task => task.state === TaskState.RUNNING)
    .forEach(task => watchTask(SECTION, task, names.get(task.job) ?? task.job, describeRunNow));
  const failed = data.jobs.filter(job => job.valid && job.state === 'failed').length;
  const invalid = data.jobs.filter(job => !job.valid).length;
  const pending = data.jobs.length - failed - invalid;
  const summary = [pending && `${pending} pending`, failed && `${failed} failed`, invalid && `${invalid} invalid`]
    .filter(Boolean).join(' · ') || 'no jobs';
  return {node: root, summary, level: worst([checker.level, failed || invalid ? Level.ERROR : Level.OK])};
}

export function searchItems(data) {
  const add = {title: 'Add a deferred job', subtitle: 'Queue a one-off future job', keywords: ['deferred', 'schedule', 'new', 'create'],
    anchor: 'add-job'};
  const addAgent = {title: 'Add an agent job', subtitle: 'Queue a headless agent prompt on this Mac or vanpi',
    keywords: ['deferred', 'agent', 'claude', 'pi', 'vanpi', 'prompt', 'follow-up'], anchor: 'add-agent-job'};
  return [add, addAgent, ...data.jobs.filter(job => job.valid).map(job => ({
    title: job.name, subtitle: `deferred · due ${job.due}`, keywords: ['deferred', 'job', job.description, job.id],
    anchor: jobAnchor(job),
    actions: [{label: 'Run now', run: () => stageAction(SECTION, jobAnchor(job), `run-${job.id}`, {arm: true})}],
  }))];
}

export function attentionItems(data) {
  const items = [];
  if (!data.checker.agent_loaded) items.push({level: Level.ERROR, text: 'the checker agent is not loaded'});
  else if (data.checker.stale) items.push({level: Level.WARN, text: 'the checker has not run recently'});
  data.jobs.forEach(job => {
    if (!job.valid) items.push({level: Level.ERROR, text: `${job.id} is an invalid job`, anchor: jobAnchor(job)});
    else if (job.state === 'failed') items.push({level: Level.ERROR, text: `${job.name} failed`, anchor: jobAnchor(job)});
    else if (job.remote_state === LAUNCH_UNKNOWN) {
      items.push({level: Level.WARN, text: `${job.name}: its Pi launch could not be found`, anchor: jobAnchor(job)});
    }
  });
  return items;
}
