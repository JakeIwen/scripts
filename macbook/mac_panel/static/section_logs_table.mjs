// Logs table rows: the per-file policy editor, rotate and ignore controls, and the ignored list.
import {anchor, button, confirmText, details, fieldError, info, node, settingRow, tag} from './panel_dom.mjs';
import {action, refresh} from './panel_sections.mjs';
import {stageAction} from './search_actions.mjs';
import {LOGS_COPY as COPY} from './section_copy.mjs';
import {Level} from './status_cell.mjs';
import {basename, formatBytes, formatGrowth, hasChanges, policyBody, policyText, problemsFromError, validatePolicy} from './logs_format.mjs';

export const SECTION = 'logs';
const ENDPOINT = '/api/logs';
const STATE_LEVEL = {ok: Level.OK, due: Level.WARN, disabled: Level.IDLE, missing: Level.IDLE, refused: Level.WARN};
const NOT_ROTATABLE = new Set(['missing', 'refused']);

export const logAnchor = path => `log-${path}`;

export function pathCell(entry) {
  const cell = anchor(node('span'), logAnchor(entry.path));
  const name = node('code', basename(entry.path), 'copyable');
  name.title = entry.path;
  cell.append(name);
  if (entry.note) cell.append(' ', info(entry.note));
  return cell;
}

export function stateCell(entry) {
  const cell = node('span');
  const label = tag(COPY.states[entry.state] ?? entry.state, STATE_LEVEL[entry.state]);
  label.title = COPY.stateHelp[entry.state] ?? '';
  cell.append(label);
  return cell;
}

export function sizeCell(entry) {
  const cell = node('span', formatBytes(entry.bytes));
  cell.title = `${entry.bytes} bytes`;
  return cell;
}

export function archivesCell(entry) {
  return node('span', entry.archives ? `${entry.archives} · ${formatBytes(entry.archived_bytes)}` : 'none', entry.archives ? '' : 'muted');
}

export const growthCell = entry => node('span', formatGrowth(entry.growth_mb_per_day));

// ---- policy editor ----

function editorControls(entry) {
  const {policy} = entry;
  const key = entry.path;
  const max = node('input');
  max.type = 'number';
  max.min = '1';
  max.step = 'any';
  max.value = String(policy.max_mb);
  max.defaultValue = max.value;
  const keep = node('input');
  keep.type = 'number';
  keep.min = '0';
  keep.value = String(policy.keep);
  keep.defaultValue = keep.value;
  const flags = {};
  ['compress', 'enabled'].forEach(name => {
    flags[name] = node('input');
    flags[name].type = 'checkbox';
    flags[name].checked = policy[name];
    flags[name].defaultChecked = policy[name];
  });
  [[max, 'max'], [keep, 'keep'], [flags.compress, 'gz'], [flags.enabled, 'on']].forEach(([input, name]) => {
    input.id = `log-${name}-${key}`;
    input.dataset.key = `policy-${name}-${key}`;
  });
  return {max, keep, ...flags};
}

function policyForm(entry, data) {
  const form = node('form', null, 'add-job');
  const inputs = editorControls(entry);
  const rows = {
    max_mb: settingRow({label: 'Rotate above', control: inputs.max, unit: 'MB'}),
    keep: settingRow({label: 'Keep', control: inputs.keep, unit: 'archives'}),
    compress: settingRow({label: 'Compress archives', control: inputs.compress}),
    enabled: settingRow({label: 'Rotation on', control: inputs.enabled}),
  };
  form.append(...Object.values(rows));
  const messages = node('div');
  const show = found => {
    form.querySelectorAll('.field-error').forEach(element => element.remove());
    Object.entries(found).forEach(([name, text]) => (rows[name] ? rows[name].after(fieldError(text)) : messages.append(fieldError(text))));
  };
  const initial = {...entry.policy};
  const values = () => ({max_mb: inputs.max.value.trim(), keep: inputs.keep.value.trim(), compress: inputs.compress.checked, enabled: inputs.enabled.checked});
  const send = (body, onError) => action(SECTION, ENDPOINT, body, {onError, reloadOnError: false});
  const conflict = text => {
    messages.replaceChildren(fieldError(`${COPY.conflict} ${text}`), button('Reload', () => refresh(SECTION), {key: `reload-${entry.path}`}));
  };
  const onError = error => (error.code === 'conflict' ? conflict(error.message) : show(problemsFromError(error.message)));
  const apply = async () => {
    messages.replaceChildren();
    const current = values();
    const found = validatePolicy(current);
    show(found);
    if (Object.keys(found).length) return;
    const body = policyBody(entry.path, data.revision, current, initial);
    if (!hasChanges(body)) {
      show({form: 'Nothing changed.'});
      return;
    }
    await send(body, onError);
  };
  form.addEventListener('submit', event => event.preventDefault());
  const controls = node('div', null, 'actions');
  controls.append(button('Apply', apply, {key: `policy-apply-${entry.path}`}));
  if (entry.policy.override) {
    controls.append(button('Reset to defaults', () => send({action: 'policy', path: entry.path, revision: data.revision, clear: true}, onError),
      {key: `policy-reset-${entry.path}`, title: `Defaults: ${data.defaults.max_mb} MB, keep ${data.defaults.keep}`}));
  }
  form.append(messages, controls);
  return form;
}

function policyCell(entry, data) {
  const cell = node('div');
  const summary = node('span');
  summary.append(policyText(entry.policy), entry.policy.override ? ' ' : '', entry.policy.override ? tag('custom') : '');
  const form = policyForm(entry, data);
  const editor = details('Edit policy', form, `policy-${entry.path}`);
  // An open editor holds back polls so typing is never replaced by a render.
  editor.addEventListener('toggle', () => { if (editor.open) form.dataset.editing = ''; else delete form.dataset.editing; });
  cell.append(summary, editor);
  return cell;
}

// ---- row actions ----

export function rotateAllDue(data) {
  const due = data.logs.filter(entry => entry.state === 'due').length;
  return button(`Rotate all due (${due})`, () => action(SECTION, ENDPOINT, {action: 'rotate', confirmed: true}),
    {confirm: confirmText(`rotate ${due} due log${due === 1 ? '' : 's'}`), key: 'rotate-all', disabled: due === 0,
      title: due === 0 ? 'No log is past its limit' : COPY.rotateAllInfo});
}

function rowActions(entry, data) {
  const cell = node('div', null, 'actions');
  const blocked = NOT_ROTATABLE.has(entry.state) ? COPY.stateHelp[entry.state] : null;
  cell.append(button('Rotate now', () => action(SECTION, ENDPOINT, {action: 'rotate', path: entry.path, force: true, confirmed: true}),
    {confirm: confirmText(`rotate ${basename(entry.path)}`), key: `rotate-${entry.path}`, disabled: Boolean(blocked), title: blocked ?? COPY.rotateRowInfo}),
  button('Ignore', () => action(SECTION, ENDPOINT, {action: 'ignore', path: entry.path, revision: data.revision}),
    {key: `ignore-${entry.path}`, title: COPY.ignoreInfo}));
  return cell;
}

export function logRow(entry, data) {
  return [pathCell(entry), entry.source, sizeCell(entry), archivesCell(entry), growthCell(entry),
    policyCell(entry, data), stateCell(entry), rowActions(entry, data)];
}

// ---- ignored list ----

export function ignoredBlock(data) {
  if (!data.ignored.length) return null;
  const list = node('ul', null, 'findings');
  data.ignored.forEach(path => {
    const item = node('li');
    item.append(node('code', path), ' ', button('Unignore', () => action(SECTION, ENDPOINT, {action: 'unignore', path, revision: data.revision}),
      {key: `unignore-${path}`}));
    list.append(item);
  });
  return details(`${COPY.ignoredTitle} (${data.ignored.length})`, list, 'ignored');
}

export const stageRotate = path => stageAction(SECTION, logAnchor(path), `rotate-${path}`, {arm: true});
