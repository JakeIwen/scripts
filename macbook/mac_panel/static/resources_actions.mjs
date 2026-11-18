// Signal actions for the Resources page: request bodies, the armed buttons and the result line. The backend
// decides what may be signalled; the page only explains why a row has no buttons.
import {button, confirmText, info, node, notice} from './panel_dom.mjs';
import {action} from './panel_sections.mjs';
import {RESOURCES_COPY as COPY} from './section_copy.mjs';
import {plural, shortFolder} from './resources_format.mjs';
import {UNPROVEN_NAME_SOURCES} from './resources_names.mjs';

export const SECTION = 'resources';
const ENDPOINT = '/api/resources';
const LOWERED_NICE = 10;

export const ActionId = Object.freeze({TERMINATE: 'terminate', KILL: 'kill', PAUSE: 'pause', RESUME: 'resume', LOWER: 'lower-priority'});
export const Scope = Object.freeze({PROCESS: 'process', TREE: 'tree'});
// The reply counts, in the order the result line lists them after "applied". `not_reached` counts the targets an
// action that stopped early never got to; `skipped` is only for members the policy protects.
const REPLY_COUNTS = ['gone', 'changed', 'skipped', 'refused', 'unchanged', 'not_reached'];
// Lowering a process that is already low is the goal reached, so it is the one action whose `unchanged` is no problem.
const NO_OP_IS_SUCCESS = new Set([ActionId.LOWER]);
const identityKey = (prefix, target) => `${prefix}-${target.pid}-${target.started}`;

/** What the backend's mutate() expects: identity (pid + start token), scope and the explicit confirmation. */
export function requestBody(actionId, target, scope = Scope.PROCESS) {
  return {action: actionId, pid: target.pid, started: target.started, scope, confirmed: true};
}

/** The actions a single process row offers, in button order. Pause and Resume share a slot; Lower priority hides at nice 10. */
export function processActions(row) {
  const ids = [ActionId.TERMINATE, ActionId.KILL, row.stopped ? ActionId.RESUME : ActionId.PAUSE];
  if (!(row.nice >= LOWERED_NICE)) ids.push(ActionId.LOWER);
  return ids;
}

/** One sentence from a mutate reply (seven flat counts and `stopped`, {code, text} or null); zero counts are left out. */
export function resultText(actionId, counts) {
  const applied = Number(counts.applied) || 0;
  const onlyNoOp = !applied && NO_OP_IS_SUCCESS.has(actionId) && REPLY_COUNTS.every(key => key === 'unchanged' || !Number(counts[key]));
  const parts = applied ? [`${COPY.actions[actionId].done} ${plural(applied, 'process', 'processes')}`] : onlyNoOp ? [] : [COPY.results.none];
  REPLY_COUNTS.forEach(key => {
    const count = Number(counts[key]) || 0;
    if (count) parts.push(`${plural(count, 'process', 'processes')} ${COPY.results[key]}`);
  });
  const line = parts.join(', ');
  // The backend words the reason itself; the page only puts it after the counts.
  return counts.stopped ? `${line}. ${COPY.results.stopped}: ${counts.stopped.text}` : line;
}

// A result that stopped early, or signalled nothing while something was refused or changed is shown as a problem, not a success.
const resultKind = (actionId, counts) => {
  if (counts?.stopped) return 'error';
  const ignored = NO_OP_IS_SUCCESS.has(actionId) ? ['gone', 'unchanged'] : ['gone'];
  return Number(counts?.applied) > 0 || !REPLY_COUNTS.some(key => !ignored.includes(key) && Number(counts?.[key]) > 0) ? 'ok' : 'error';
};

export function runAction(actionId, target, scope = Scope.PROCESS) {
  return action(SECTION, ENDPOINT, requestBody(actionId, target, scope), {
    after: reply => notice(SECTION, resultText(actionId, reply), resultKind(actionId, reply)),
  });
}

const processLabel = row => `${row.name} (pid ${row.pid})`;

/** Confirmation wording names what will be signalled; counts are snapshot counts. */
export const confirmPhrase = (actionId, row) => `${COPY.actions[actionId].verb} ${processLabel(row)}`;

function actionButton(actionId, row) {
  return button(COPY.actions[actionId].label, () => runAction(actionId, row), {
    confirm: confirmText(confirmPhrase(actionId, row)), key: rowKey(actionId, row), className: 'secondary',
  });
}

/** Buttons for a process row, or only the reason behind an info popover when the policy blocks it. */
export function rowActions(row) {
  const cell = node('div', null, 'actions');
  if (row.blocked) {
    cell.append(node('span', COPY.processes.notSignalled, 'muted'), info(row.blocked));
    return cell;
  }
  processActions(row).forEach(id => cell.append(actionButton(id, row)));
  return cell;
}

// Every armed button is keyed by the row's identity (pid and start token), so a pid that was reused after a poll never
// matches a key staged for its predecessor.
export const rowKey = (actionId, row) => identityKey(actionId, row);
export const sessionKey = group => identityKey('session', {pid: group.root_pid, started: group.started});
export const sessionConfirm = (group, rows) => confirmText(COPY.sessions.terminateConfirm({
  kind: COPY.kinds[group.kind] ?? group.kind, name: group.name, pid: group.root_pid, folder: shortFolder(group.cwd),
  seen: `${plural(rows, 'process', 'processes')} seen`, unconfirmed: UNPROVEN_NAME_SOURCES.includes(group.name_source)}));

/** "Terminate session" for a session group; absent, with the reason, when the group has a nested session. */
export function sessionAction(group) {
  const row = node('div', null, 'actions');
  if (group.tree_blocked) {
    row.append(node('span', COPY.sessions.treeBlocked, 'muted'), info(group.tree_blocked));
    return row;
  }
  const target = {pid: group.root_pid, started: group.started, name: group.name};
  row.append(button(COPY.sessions.terminate, () => runAction(ActionId.TERMINATE, target, Scope.TREE),
    {confirm: sessionConfirm(group, group.count ?? group.processes?.length ?? 0), key: sessionKey(group)}),
  info(COPY.sessions.info.terminate));
  return row;
}
