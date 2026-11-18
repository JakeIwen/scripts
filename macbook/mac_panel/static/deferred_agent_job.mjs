// What an agent job's card adds: host/model/effort tags, its Pi run's state, the confirm wording for a
// retry that may start another Pi run, and the outcome wording for a run-now that leaves its run going.
import {confirmText, info, node, tag} from './panel_dom.mjs';
import {JOBS_COPY} from './section_copy.mjs';
import {Level} from './status_cell.mjs';

const COPY = JOBS_COPY.deferred.agent;

// deferred_pi_run.py's RemoteState, each with the level its tag shows.
export const REMOTE_LEVEL = Object.freeze({
  unlaunched: Level.IDLE,
  launching: Level.WARN,
  'launch-unknown': Level.ERROR,
  running: Level.OK,
  finished: Level.OK,
  failed: Level.ERROR,
});
export const LAUNCH_UNKNOWN = 'launch-unknown';
// deferred.zsh run-now exits 4 for "not ready", and for a vanpi job whose run is still going and is collected later.
const RUN_NOW_PENDING = 4;
const ACTIVE = ['running', 'launching'];

/** The extra request fields for retry/run-now: the unresolved Pi attempt's id (`relaunch_id`, as the list shows it),
 * which the CLI checks under its lock; a job with nothing unresolved sends none. */
export function relaunchFields(job) {
  return job.relaunch_id ? {confirmed: true, relaunch: job.relaunch_id} : {};
}

// run-now's exit-4 lines; a relaunch prints what it cleared before the "running" line.
const STILL_RUNNING = /^running /m;
const NOT_READY = /^not ready .*$/m;

/** run-now's outcome wording from the finished task alone, for the click and the resumed-task paths. Exit 4 is not a
 * failure: a "running ..." line is a Pi run left going, collected by a later check, and a "not ready ..." line (the
 * CLI's own, saying when the checker tries again) is shown as it is. */
export function describeRunNow(task) {
  if (task.exit_status !== RUN_NOW_PENDING) return undefined;
  const message = task.message ?? '';
  return STILL_RUNNING.test(message) ? COPY.collectedLater : message.match(NOT_READY)?.[0];
}

/** The Pi attempt a card shows (its launch token, else its run id), or '' when there is none. */
export const attemptOf = job => job.remote_token || job.remote_run || '';

/** The card's version: only a job whose Pi run is still running or launching is shown as collected later, so a
 * receipt that predates the run's end does not keep saying it is running. */
export function collectedLaterOnCard(job, task) {
  return job.host === 'vanpi' && ACTIVE.includes(job.remote_state) && task.exit_status === RUN_NOW_PENDING;
}

export const collectedLaterText = COPY.collectedLater;

/** The card's status for its latest panel run, or undefined for the plain done/failed wording. Exit 4 is never shown
 * as a failure: a Pi run still going is "collected later", a "not ready ..." line is shown as the CLI wrote it, and a
 * run-now whose Pi run has since ended only says it started that run (the job's own state says how it ended). */
export function cardOutcome(job, task) {
  if (task.exit_status !== RUN_NOW_PENDING) return undefined;
  if (collectedLaterOnCard(job, task)) return {level: Level.OK, text: COPY.collectedLater};
  const message = task.message ?? '';
  const notReady = message.match(NOT_READY)?.[0];
  if (notReady) return {level: Level.IDLE, text: notReady};
  return STILL_RUNNING.test(message) ? {level: Level.IDLE, text: COPY.startedRun} : undefined;
}

/** A vanpi job that has launched is moved on only by retry or run-now, so it has no Reschedule. */
export const rescheduleLocked = job => job.host === 'vanpi' && job.remote_state !== 'unlaunched';

/** Tags for an agent job's card, or null for a script job. */
export function agentTags(job) {
  if (!job.agent || !job.host) return null;
  const line = node('p', null, 'agent-tags');
  line.append(tag(COPY.hosts[job.host] ?? job.host), tag(job.model ?? 'default model'), tag(`effort ${job.effort ?? 'default'}`));
  if (job.remote_state) {
    line.append(tag(`Pi run: ${job.remote_state}`, REMOTE_LEVEL[job.remote_state] ?? Level.UNKNOWN), info(COPY.remoteInfo));
  }
  return line;
}

/** The Details rows naming the Pi run, when the job has one. */
export function remoteRows(job) {
  return job.remote_run ? [['Pi run', job.remote_run]] : [];
}

/** Retry and run-now over an unresolved Pi attempt may start another run beside it, so say so and name it. */
export function relaunchConfirm(job, phrase) {
  return job.relaunch_id ? confirmText(COPY.relaunch(job.relaunch_id)) : phrase && confirmText(phrase);
}
