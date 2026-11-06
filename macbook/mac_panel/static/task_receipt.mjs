// Background-task receipts: the panel starts a slow tool and answers at once with a task id; this module
// polls GET /api/tasks/<id> until it finishes, then reports the outcome and refreshes the section.
import {api} from './panel_api.mjs';
import {details, node, notice, timeNode} from './panel_dom.mjs';
import {refresh} from './panel_sections.mjs';
import {Level, status} from './status_cell.mjs';

export const TaskState = Object.freeze({RUNNING: 'running', DONE: 'done', FAILED: 'failed'});
const POLL_MS = 2000;
const NOTICE_CHARS = 240;
// Receipt ids already being polled, so a re-render of a running task never starts a second poll.
const watching = new Set();

const clip = text => (text.length > NOTICE_CHARS ? `${text.slice(0, NOTICE_CHARS)}…` : text);

function outcomeText(label, task) {
  const exit = task.exit_status === null || task.exit_status === undefined ? 'unknown' : task.exit_status;
  return clip(`${label}: ${task.state}, exit ${exit}${task.message ? `: ${task.message}` : ''}`);
}

/** Polls a receipt ({id, ...}) every 2 s until it has a finish time, then shows the outcome and reloads `section`. */
export function watchTask(section, receipt, label) {
  if (watching.has(receipt.id)) return;
  watching.add(receipt.id);
  notice(section, `${label}: running…`, 'working');
  const poll = async () => {
    try {
      const task = await api(`/api/tasks/${encodeURIComponent(receipt.id)}`);
      if (task.finished_at === null || task.finished_at === undefined) {
        setTimeout(poll, POLL_MS);
        return;
      }
      watching.delete(receipt.id);
      notice(section, outcomeText(label, task), task.state === TaskState.FAILED ? 'error' : 'ok');
      await refresh(section);
    } catch (error) {
      watching.delete(receipt.id);
      notice(section, error.message, 'error');
    }
  };
  setTimeout(poll, POLL_MS);
}

/** After a reload, picks up tasks still running (started before the page was opened or from another tab). */
export function resumeTasks(section, tasks, labelOf) {
  tasks.filter(task => task.state === TaskState.RUNNING).forEach(task => watchTask(section, task, labelOf(task)));
}

export const latestTask = (tasks, job) =>
  tasks.filter(task => task.job === job).sort((a, b) => b.started_at - a.started_at)[0];

/** One status line for a task receipt, with the tool's output in a disclosure. */
export function taskLine(task) {
  const when = timeNode(task.finished_at ?? task.started_at);
  const detail = node('span');
  if (task.state === TaskState.RUNNING) {
    detail.append('started ', when);
    return status(Level.IDLE, 'running from the panel…', detail);
  }
  detail.append(`exit ${task.exit_status ?? 'unknown'} · `, when);
  const line = node('div');
  const failed = task.state === TaskState.FAILED;
  line.append(status(failed ? Level.ERROR : Level.OK, failed ? 'panel run failed' : 'panel run done', detail));
  if (task.message && task.message !== 'Running') line.append(details('Output', task.message, `task-${task.id}`));
  return line;
}
