// What an agent job's card adds: host/model/effort tags, its Pi run's state, and the confirm wording
// for a retry that may start a second Pi run.
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

/** Retry and run-now after a launch that could not be reconciled may start a second Pi run, so say so. */
export function relaunchConfirm(job, phrase) {
  return job.remote_state === LAUNCH_UNKNOWN ? confirmText(COPY.relaunch) : phrase && confirmText(phrase);
}
