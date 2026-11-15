// llm-restore snapshots: pick the automatic restore list or a saved set, read its dry run, restore it (armed),
// or save the sessions open now. The CLI owns every set; this only relays `/api/llm-restore/snapshots`.
import {api} from './panel_api.mjs';
import {button, card, confirmText, info, node, notice, timeNode} from './panel_dom.mjs';
import {action} from './panel_sections.mjs';
import {JOBS_COPY} from './section_copy.mjs';

const SECTION = 'llm-restore';
const ENDPOINT = '/api/llm-restore/snapshots';
const COPY = JOBS_COPY.restore;
export const SNAPSHOT_ANCHOR = 'snapshot-list';
export const NAME_KEY = 'snapshot-name';
export const AUTO_KEY = 'pick-auto';
const AUTO = 'auto';

// State outlives a render because every refresh rebuilds the section. The preview belongs to one
// selected source (`stamp` is that source's timestamp and count when it was fetched); a report belongs
// to the action that produced it, so selecting another row never re-attributes it. `gen` numbers preview
// requests so only the newest response is applied; `paint` redraws the output of the latest render.
const view = {key: null, preview: null, report: null, gen: 0, paint: null};

// Test seam: the module keeps selection across renders on purpose, so tests start from nothing.
export function resetSnapshotView() {
  Object.assign(view, {key: null, preview: null, report: null, gen: view.gen + 1, paint: null});
}

const targetOf = key => key === AUTO ? {kind: 'auto'} : {kind: 'saved', name: key.slice('saved:'.length)};
const labelOf = key => key === AUTO ? 'the latest automatic snapshot' : key.slice('saved:'.length);

function stampOf(data, key) {
  if (key === AUTO) return `auto:${data.last_snapshot}`;
  const set = data.saved.find(item => `saved:${item.name}` === key);
  return `${set.path}:${set.saved_at}:${set.sessions}`;
}

function row({key, title, saved_at, detail}, select) {
  const pick = node('button', null, 'snapshot-pick');
  pick.type = 'button';
  pick.dataset.key = key === AUTO ? AUTO_KEY : `pick-${key}`;
  pick.dataset.target = key;
  pick.setAttribute('aria-pressed', String(view.key === key));
  pick.append(node('strong', title), timeNode(saved_at, 'never'), node('span', detail, 'muted'));
  pick.addEventListener('click', () => select(key));
  const item = node('li');
  item.append(pick);
  return item;
}

function textBlock(title, text, className) {
  const block = node('div', null, 'snapshot-output');
  block.append(node('div', title, 'muted small'), node('pre', text, className));
  return block;
}

function loadPreview(key, stamp) {
  const gen = ++view.gen;
  view.preview = {key, stamp, pending: true};
  api(ENDPOINT, {action: 'preview', target: targetOf(key)}).then(result => {
    if (gen !== view.gen) return;
    view.preview = {key, stamp, text: result.report};
    view.paint?.();
  }, error => {
    if (gen !== view.gen) return;
    view.preview = {key, stamp, error: error.message};
    notice(SECTION, error.message, 'error');
    view.paint?.();
  });
}

// A preview is reused only for the same source with unchanged data.
function ensurePreview(key, stamp) {
  const current = view.preview;
  if (!current || current.key !== key || current.stamp !== stamp) loadPreview(key, stamp);
}

function paintOutput(slot) {
  const parts = [];
  if (view.report) parts.push(textBlock(view.report.title, view.report.text, 'snapshot-pre'));
  const preview = view.preview;
  if (view.key && preview?.key === view.key) {
    if (preview.pending) parts.push(node('p', 'Loading preview…', 'muted small'));
    else if (preview.error) parts.push(node('p', `Preview failed: ${preview.error}`, 'muted small'));
    else parts.push(textBlock('Dry run: what a restore would do', preview.text, 'snapshot-pre'));
  }
  slot.replaceChildren(...parts);
}

function restoreControl(key) {
  const wrap = node('div', null, 'actions');
  const label = labelOf(key);
  wrap.append(button('Restore', () => action(SECTION, ENDPOINT, {action: 'restore', target: targetOf(key), confirmed: true}, {
    after: result => {
      view.report = {title: `Restore report: ${label}`, text: result.report};
      view.preview = null; // what a restore would do has changed; the refresh fetches it again
    },
  }), {confirm: confirmText(`restore ${label}`), key: 'restore-selected'}), info(COPY.restoreInfo));
  return wrap;
}

function snapshotForm() {
  const form = node('div', null, 'actions');
  const input = node('input');
  input.type = 'text';
  input.maxLength = 64;
  input.autocomplete = 'off';
  input.placeholder = 'name (optional)';
  input.dataset.key = NAME_KEY;
  input.setAttribute('aria-label', 'Snapshot name');
  const take = () => action(SECTION, ENDPOINT, {action: 'save', ...(input.value.trim() ? {name: input.value.trim()} : {})}, {
    after: result => { view.report = {title: 'Snapshot report', text: result.report}; },
  });
  input.addEventListener('keydown', event => { if (event.key === 'Enter') take(); });
  form.append(input, button('Take snapshot', take, {key: 'take-snapshot'}), info(COPY.takeInfo));
  return form;
}

export function snapshotCard(data) {
  const keys = [AUTO, ...data.saved.map(set => `saved:${set.name}`)];
  if (!keys.includes(view.key)) view.key = null;
  const list = node('ul', null, 'snapshot-list');
  const control = node('div');
  const output = node('div', null, 'snapshot-output-slot');
  const stage = node('div', null, 'snapshot-stage');
  stage.append(control, output);
  view.paint = () => paintOutput(output);
  const show = () => {
    control.replaceChildren(...(view.key ? [restoreControl(view.key)] : []));
    if (view.key) ensurePreview(view.key, stampOf(data, view.key));
    view.paint();
  };
  const choose = key => {
    view.key = key;
    view.preview = null; // selecting again always asks for a fresh dry run
    list.querySelectorAll('button.snapshot-pick').forEach(pick => pick.setAttribute('aria-pressed', String(pick.dataset.target === key)));
    show();
  };
  list.append(row({key: AUTO, title: 'Latest automatic snapshot', saved_at: data.last_snapshot, detail: `${data.sessions_open ?? '?'} open now`}, choose));
  data.saved.forEach(set => list.append(row({key: `saved:${set.name}`, title: set.name, saved_at: set.saved_at, detail: `${set.sessions} sessions`}, choose)));
  const body = node('div');
  body.append(snapshotForm(), list, stage);
  show();
  return card({title: 'Restore sessions', body, anchor: SNAPSHOT_ANCHOR});
}
