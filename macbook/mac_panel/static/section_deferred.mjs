// Deferred jobs: the deferred.zsh CLI owns the jobs; the page shows them and relays explicit commands.
import {api} from './panel_api.mjs';
import {PLACEHOLDER, button, details, keyValues, node, notice, table, timeNode, timeWithAbsolute} from './panel_dom.mjs';
import {action, refresh} from './panel_sections.mjs';
import {Level, status, worst} from './status_cell.mjs';

const SECTION = 'deferred';
const taskStates = {running: 'running', done: 'done', failed: 'failed'};
const TASK_POLL_MS = 2000;

function apply(job, kind, extra = {}, options = {}) {
  return action(SECTION, '/api/deferred', {action: kind, id: job.id, ...extra}, options);
}

async function watchTask(id) {
  try {
    const task = await api(`/api/tasks/${id}`);
    if (task.state === taskStates.running) {
      setTimeout(() => watchTask(id), TASK_POLL_MS);
      return;
    }
    notice(SECTION, `${task.job}: ${task.state}, exit ${task.exit_status ?? 'unknown'}: ${task.message}`,
      task.state === taskStates.failed ? 'error' : 'ok');
    await refresh(SECTION);
  } catch (error) {
    notice(SECTION, error.message, 'error');
  }
}

function checkerLine(checker) {
  const level = !checker.agent_loaded ? Level.ERROR : checker.stale ? Level.WARN : Level.OK;
  const detail = node('span');
  detail.append(checker.stale ? 'stale · ' : '', 'last run ', timeNode(checker.last_run));
  const line = node('p', null, 'summary-line');
  line.append(status(level, checker.agent_loaded ? 'Checker agent loaded' : 'Checker agent NOT loaded', detail));
  return {line, level};
}

function latestTask(tasks, jobId) {
  return tasks.filter(task => task.job === jobId).sort((a, b) => b.started_at - a.started_at)[0];
}

function taskLine(task) {
  const when = timeNode(task.finished_at ?? task.started_at);
  const detail = node('span');
  if (task.state === taskStates.running) {
    detail.append('started ', when);
    return status(Level.IDLE, 'running from the panel…', detail);
  }
  detail.append(`exit ${task.exit_status ?? 'unknown'} · `, when);
  const line = node('div');
  const failed = task.state === taskStates.failed;
  line.append(status(failed ? Level.ERROR : Level.OK, failed ? 'panel run failed' : 'panel run done', detail));
  if (task.message && task.message !== 'Running') line.append(details('Output', task.message, `task-${task.id}`));
  return line;
}

function jobDetails(job) {
  const body = node('div');
  body.append(keyValues([['Created', timeWithAbsolute(job.created)], ['Origin', job.origin],
    ['Timeout', `${job.timeout} s`], ['Attempts', `${job.attempts}/${job.max_attempts}`], ['Id', job.id]]));
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

function jobActions(job) {
  const cell = node('div', null, 'actions');
  const started = result => {
    notice(SECTION, `Running ${job.name} from the panel…`, 'working');
    watchTask(result.id);
  };
  cell.append(button('Run now', () => apply(job, 'run-now', {}, {after: started}),
    {confirm: 'Confirm run now', key: `run-${job.id}`}));
  if (job.state === 'failed') cell.append(button('Retry', () => apply(job, 'retry'), {key: `retry-${job.id}`}));
  cell.append(button('Reschedule…', () => rescheduleEditor(job, cell), {key: `reschedule-${job.id}`}));
  cell.append(button('Cancel job', () => apply(job, 'cancel', {confirmed: true}),
    {confirm: 'Confirm cancel job', key: `cancel-${job.id}`}));
  return cell;
}

function jobRow(job, tasks) {
  if (!job.valid) {
    return [node('strong', job.id), status(Level.ERROR, 'invalid job', 'inspect in Terminal'),
      PLACEHOLDER, PLACEHOLDER, PLACEHOLDER];
  }
  const name = node('div');
  name.append(node('strong', job.name), node('div', job.id, 'muted small'));
  const failed = job.state === 'failed';
  const state = node('div');
  state.append(status(failed ? Level.ERROR : Level.IDLE, job.state, `${job.attempts}/${job.max_attempts} attempts`));
  const task = latestTask(tasks, job.id);
  if (task) state.append(taskLine(task));
  const description = node('div');
  description.append(node('p', job.description), jobDetails(job));
  return [name, state, timeWithAbsolute(job.due), description, jobActions(job)];
}

function orphanTasks(tasks) {
  const body = node('div');
  tasks.forEach(task => body.append(node('div', `${task.job}: `, null), taskLine(task)));
  return details(`Panel runs of jobs no longer listed (${tasks.length})`, body, 'orphan-tasks');
}

export function render(data) {
  const root = node('div');
  const checker = checkerLine(data.checker);
  root.append(checker.line);
  const rows = data.jobs.map(job => jobRow(job, data.tasks));
  root.append(rows.length ? table(['Job', 'State', 'Due', 'Description', 'Actions'], rows) : node('p', 'No deferred jobs'));
  const orphans = data.tasks.filter(task => !data.jobs.some(job => job.id === task.job));
  if (orphans.length) root.append(orphanTasks(orphans));
  root.append(node('p', `Jobs directory: ${data.jobs_dir}`, 'muted small'));
  const failed = data.jobs.filter(job => job.valid && job.state === 'failed').length;
  const invalid = data.jobs.filter(job => !job.valid).length;
  const pending = data.jobs.length - failed - invalid;
  const summary = [pending && `${pending} pending`, failed && `${failed} failed`, invalid && `${invalid} invalid`]
    .filter(Boolean).join(' · ') || 'no jobs';
  return {node: root, summary, level: worst([checker.level, failed || invalid ? Level.ERROR : Level.OK])};
}
