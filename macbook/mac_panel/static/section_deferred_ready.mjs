// A deferred job's ready condition: what decides "not ready yet", and when a not-ready answer stops being tolerated.
import {details, kv, node, timeNode} from './panel_dom.mjs';
import {absoluteText, parseStamp} from './relative_time.mjs';
import {JOBS_COPY} from './section_copy.mjs';

const COPY = JOBS_COPY.deferred.ready;

/** Pending, past due, and the latest check said not ready: waiting on its condition, not stuck. */
export function waitingNotReady(job, now = Date.now()) {
  return job.state === 'pending' && Boolean(job.last_not_ready) && parseStamp(job.due) <= now;
}

/** The status word on the job card: its state, or why a past-due pending job has not run. */
export const stateLabel = (job, now) => waitingNotReady(job, now) ? COPY.waiting : job.state;

function conditionBlock(job) {
  if (job.ready) return node('pre', job.ready, 'ready-command');
  return node('p', job.agent ? COPY.none : COPY.ownScript, 'muted');
}

function lastCheck(job) {
  const when = parseStamp(job.last_not_ready);
  if (when === null) return undefined;
  const count = job.not_ready_checks;
  return `not ready at ${absoluteText(when)}${count ? ` (${count} ${count === 1 ? 'check' : 'checks'})` : ''}`;
}

function givesUp(job) {
  if (parseStamp(job.give_up) === null) return undefined;
  const value = node('span');
  value.append(`${absoluteText(parseStamp(job.give_up))} (`, timeNode(job.give_up), ')');
  return value;
}

export function readyDetails(job) {
  const body = node('div');
  body.append(conditionBlock(job), kv([['Last check', lastCheck(job)], ['Gives up', givesUp(job)]]));
  return details(COPY.title, body, `ready-${job.id}`);
}
