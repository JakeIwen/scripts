// "Apps and processes": a By app / By process choice, a CPU / Memory sort, a Paused chip and a text filter over every
// process in the snapshot. Filter and sort run in memory over all rows; only the first WINDOW_ROWS matches become DOM.
// The choices live in module state so a 15 s re-render keeps them, and changing one redraws only the list, so typing in
// the filter never loses focus.
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
export const view = {mode: ViewMode.APP, sort: SortKey.CPU, filter: '', paused: false};
// Rows built per render: enough to scan, cheap to draw; the filter reaches the rest.
export const WINDOW_ROWS = 50;

const lower = value => String(value ?? '').toLowerCase();
const byName = (a, b) => lower(a.name).localeCompare(lower(b.name));

/** Rows matching the filter text (name, PID or group kind) and, with `paused`, only stopped ones; busiest first by the chosen key. */
export function visibleRows(rows, {sort, filter, paused = false}) {
  const terms = lower(filter).split(/\s+/).filter(Boolean);
  return rows
    .filter(row => !paused || row.stopped)
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
  [ViewMode.APP]: {rows: data => data.apps ?? [], pausedApplies: false, heads: COPY_PROCESSES.appHeads, row: APP_ROW, numeric: [1, 2, 3, 4, 5]},
  [ViewMode.PROCESS]: {rows: data => data.processes ?? [], pausedApplies: true, heads: COPY_PROCESSES.processHeads, row: PROCESS_ROW, numeric: [1, 2, 3, 4]},
};

/** The rows to draw and the match count behind them: the first WINDOW_ROWS of everything that passes filter and sort. */
export function listWindow(spec, data, state = view) {
  const matching = visibleRows(spec.rows(data), {...state, paused: state.paused && spec.pausedApplies});
  return {rows: matching.slice(0, WINDOW_ROWS), matching: matching.length};
}

function listBody(data) {
  const spec = VIEWS[view.mode];
  const body = node('div');
  if (!spec.rows(data).length) return emptyState(COPY_PROCESSES.none);
  const {rows, matching} = listWindow(spec, data);
  if (!matching) return emptyState(COPY_PROCESSES.noMatch);
  body.append(numberTable(spec.heads, rows.map(spec.row), spec.numeric));
  const narrowed = view.filter.trim() !== '' || (view.paused && spec.pausedApplies);
  body.append(node('p', COPY_PROCESSES.shown(rows.length, matching, narrowed), 'muted small'));
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

// A press-and-hold chip beside the filter: narrows By process to stopped rows, which are otherwise easy to lose.
function pausedChip(count, onChange) {
  const group = node('div', null, 'range-toggle');
  group.setAttribute('role', 'group');
  const chip = node('button', COPY_PROCESSES.pausedChip(count), 'range-choice');
  chip.type = 'button';
  chip.dataset.key = 'process-paused';
  chip.title = COPY_PROCESSES.pausedChipTip;
  chip.addEventListener('click', () => { view.paused = !view.paused; sync(); onChange(); });
  // A click updates the pressed state itself: a focused control pauses the poll, so no re-render would.
  const sync = () => {
    chip.setAttribute('aria-pressed', String(view.paused));
    group.hidden = !VIEWS[view.mode].pausedApplies;
  };
  group.append(chip);
  sync();
  return {group, sync};
}

export function processesCard(data) {
  const list = node('div', null, 'process-list');
  let chip = null;
  const redraw = () => { chip?.sync(); list.replaceChildren(listBody(data)); };
  const mode = segmented({label: COPY_PROCESSES.modeLabel, key: 'view', options: COPY_PROCESSES.modes, read: () => view.mode,
    write: value => { view.mode = value; }, onChange: redraw});
  const sort = segmented({label: COPY_PROCESSES.sortLabel, key: 'sort', options: COPY_PROCESSES.sorts, read: () => view.sort,
    write: value => { view.sort = value; }, onChange: redraw});
  chip = pausedChip((data.processes ?? []).filter(row => row.stopped).length, redraw);
  const controls = node('div', null, 'actions process-controls');
  const filter = filterInput(redraw);
  controls.append(mode.group, sort.group, chip.group, filter);
  const body = node('div');
  body.append(controls, list);
  redraw();
  return card({title: COPY_PROCESSES.title, body, anchor: 'processes', actions: info(COPY_PROCESSES.info.table)});
}
