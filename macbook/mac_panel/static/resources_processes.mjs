// "Apps and processes": a By app / By process choice, a CPU / Memory sort and a text filter over the
// snapshot's busiest processes. The choices live in module state so a 15 s re-render keeps them, and changing one
// redraws only the list, so typing in the filter never loses focus.
import {card, emptyState, info, node, tag} from './panel_dom.mjs';
import {RESOURCES_COPY as COPY} from './section_copy.mjs';
import {Level} from './status_cell.mjs';
import {groupKind} from './resources_groups.mjs';
import {rowActions} from './resources_actions.mjs';
import {numberTable} from './resources_table.mjs';
import {formatAge, formatBytes, formatPercent} from './resources_format.mjs';

const COPY_PROCESSES = COPY.processes;
export const ViewMode = Object.freeze({APP: 'app', PROCESS: 'process'});
export const SortKey = Object.freeze({CPU: 'cpu', MEMORY: 'rss'});
export const view = {mode: ViewMode.APP, sort: SortKey.CPU, filter: ''};
let current = null;

const lower = value => String(value ?? '').toLowerCase();
const byName = (a, b) => lower(a.name).localeCompare(lower(b.name));

/** Rows matching the filter text (name, PID or group kind), busiest first by the chosen key. */
export function visibleRows(rows, {sort, filter}) {
  const terms = lower(filter).split(/\s+/).filter(Boolean);
  return rows
    .filter(row => terms.every(term => [row.name, row.pid ?? row.root_pid, groupKind(row.group), row.user].some(field => lower(field).includes(term))))
    .sort((a, b) => (b[sort] ?? 0) - (a[sort] ?? 0) || byName(a, b));
}

function nameCell(row) {
  const cell = node('span');
  cell.append(row.name);
  const kind = groupKind(row.group);
  if (kind) cell.append(' ', tag(COPY.kinds[kind] ?? kind));
  if (row.stopped) cell.append(' ', tag(COPY_PROCESSES.pausedTag, Level.WARN));
  if (row.nice > 0) cell.append(' ', node('span', COPY_PROCESSES.niceTag(row.nice), 'muted small'));
  return cell;
}

const PROCESS_ROW = row => [nameCell(row), String(row.pid), formatPercent(row.cpu), formatBytes(row.rss), formatAge(row.started), rowActions(row)];
const APP_ROW = row => [row.name, String(row.root_pid), formatPercent(row.cpu), formatBytes(row.rss), String(row.count ?? '—'), formatAge(row.started)];
const VIEWS = {
  [ViewMode.APP]: {rows: data => data.apps ?? [], heads: COPY_PROCESSES.appHeads, row: APP_ROW, numeric: [1, 2, 3, 4, 5]},
  [ViewMode.PROCESS]: {rows: data => data.processes ?? [], heads: COPY_PROCESSES.processHeads, row: PROCESS_ROW, numeric: [1, 2, 3, 4]},
};

function listBody(data) {
  const spec = VIEWS[view.mode];
  const all = spec.rows(data);
  const shown = visibleRows(all, view);
  const body = node('div');
  if (!all.length) return emptyState(COPY_PROCESSES.none);
  if (!shown.length) body.append(emptyState(COPY_PROCESSES.noMatch));
  else body.append(numberTable(spec.heads, shown.map(spec.row), spec.numeric));
  body.append(node('p', COPY_PROCESSES.shown(shown.length, all.length), 'muted small'));
  return body;
}

// One segmented control for both choices: `options` maps a state value to its words.
function segmented({label, key, options, read, write, onChange}) {
  const group = node('div', null, 'range-toggle');
  group.setAttribute('role', 'group');
  group.setAttribute('aria-label', label);
  const buttons = [];
  // A click updates the pressed state itself: a focused filter pauses the poll, so no re-render would.
  const sync = () => buttons.forEach(({value, choice}) => choice.setAttribute('aria-pressed', String(read() === value)));
  Object.entries(options).forEach(([value, text]) => {
    const choice = node('button', text, 'range-choice');
    choice.type = 'button';
    choice.dataset.key = `${key}-${value}`;
    choice.addEventListener('click', () => { write(value); sync(); onChange(); });
    buttons.push({value, choice});
  });
  group.append(...buttons.map(entry => entry.choice));
  sync();
  return {group, sync};
}

function filterInput(onChange) {
  const input = node('input');
  input.type = 'text';
  input.value = view.filter;
  // The value counts as the rendered default, so a poll is not held back as "unsaved edits"; focus alone pauses it.
  input.defaultValue = view.filter;
  input.placeholder = COPY_PROCESSES.filterPlaceholder;
  input.autocomplete = 'off';
  input.spellcheck = false;
  input.dataset.key = 'process-filter';
  input.setAttribute('aria-label', COPY_PROCESSES.filterLabel);
  input.addEventListener('input', () => {
    view.filter = input.value;
    input.defaultValue = input.value;
    onChange();
  });
  return input;
}

export function processesCard(data) {
  const list = node('div', null, 'process-list');
  const redraw = () => list.replaceChildren(listBody(data));
  const mode = segmented({label: COPY_PROCESSES.modeLabel, key: 'view', options: COPY_PROCESSES.modes, read: () => view.mode,
    write: value => { view.mode = value; }, onChange: redraw});
  const sort = segmented({label: COPY_PROCESSES.sortLabel, key: 'sort', options: COPY_PROCESSES.sorts, read: () => view.sort,
    write: value => { view.sort = value; }, onChange: redraw});
  const controls = node('div', null, 'actions process-controls');
  const filter = filterInput(redraw);
  controls.append(mode.group, sort.group, filter);
  const body = node('div');
  body.append(controls, list);
  redraw();
  current = {redraw, sync: () => { mode.sync(); sort.sync(); filter.value = view.filter; filter.defaultValue = view.filter; }};
  return card({title: COPY_PROCESSES.title, body, anchor: 'processes', actions: info(COPY_PROCESSES.info.table)});
}

/** For a search result that must press a per-process button: shows the By process list with no filter, as the button lives only there. */
export function revealProcessList() {
  Object.assign(view, {mode: ViewMode.PROCESS, filter: ''});
  if (!current) return;
  current.sync();
  current.redraw();
}
