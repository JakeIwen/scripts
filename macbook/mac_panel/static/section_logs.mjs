// Logs: every log the Mac's jobs write, its size and growth, and the rotator's policy. Sizes and paths only.
import {sparkline} from './charts.mjs';
import {historyPanel} from './history_panel.mjs';
import {anchor, button, card, details, emptyState, fieldError, info, node, table, tag} from './panel_dom.mjs';
import {action} from './panel_sections.mjs';
import {LOGS_COPY as COPY} from './section_copy.mjs';
import {ignoredBlock, logAnchor, logRow, rotateAllDue, SECTION, stageRotate} from './section_logs_table.mjs';
import {goTo} from './shell.mjs';
import {Level, status, worst} from './status_cell.mjs';
import {basename, bySize, formatBytes, problems, validateAddPath} from './logs_format.mjs';

const HEADERS = ['Log', 'Source', 'Size', 'Archives', 'Growth', 'Policy', 'State', ''];
const HISTORY_MAX_GAP = 7200;

function rotatorState(service) {
  if (service.installed === false) return 'not installed';
  if (service.loaded === null) return 'unknown';
  return service.loaded ? 'loaded' : 'not running';
}

function titled(text, help) {
  const result = node('span', text);
  result.append(info(help));
  return result;
}

function summaryLine(data, level) {
  const line = node('p', null, 'summary-line');
  line.append(status(level, `${data.totals.files} logs · ${formatBytes(data.totals.bytes)} live · ${formatBytes(data.totals.archived_bytes)} archived · rotator ${rotatorState(data.service)}`));
  return line;
}

function helpRow() {
  const row = node('div', null, 'actions');
  [['What counts as a log', COPY.whatCounts], ['Copy-truncate', COPY.copyTruncate], ['Backups', COPY.backups], ['Growth per day', COPY.growth]]
    .forEach(([label, text]) => row.append(node('span', label, 'muted small'), info(text)));
  return row;
}

function drawHistory(series, window) {
  const points = (series.total ?? []).filter(point => point.value !== null);
  return points.length ? [sparkline({points, ...window, min: 0, format: formatBytes, maxGap: HISTORY_MAX_GAP})] : [];
}

function historyCard() {
  const body = historyPanel({id: SECTION, section: 'logs', draw: drawHistory, emptyText: COPY.noHistory});
  return card({title: titled(COPY.historyTitle, COPY.historyInfo), body, anchor: 'history'});
}

function addForm(data) {
  const form = node('form', null, 'add-job');
  const input = node('input');
  input.type = 'text';
  input.id = 'logs-add-path';
  input.dataset.key = 'add-path';
  input.placeholder = '/Users/…/example.log';
  input.autocomplete = 'off';
  input.spellcheck = false;
  const problem = node('div');
  const submit = async () => {
    const text = input.value.trim();
    const found = validateAddPath(text);
    problem.replaceChildren(...(found ? [fieldError(found)] : []));
    if (found) return;
    await action(SECTION, '/api/logs', {action: 'add', path: text, revision: data.revision}, {
      onError: error => problem.replaceChildren(fieldError(error.message)), reloadOnError: false,
    });
  };
  form.addEventListener('submit', event => { event.preventDefault(); submit(); });
  const row = node('div', null, 'actions');
  row.append(input, button('Add path', submit, {key: 'add-log'}), info(COPY.addInfo));
  form.append(row, problem);
  return anchor(details(COPY.addTitle, form, 'add-form'), 'add-log');
}

function tableCard(data) {
  const body = node('div');
  const head = node('div', null, 'actions');
  head.append(rotateAllDue(data), helpRow());
  body.append(head);
  const rows = bySize(data.logs).map(entry => logRow(entry, data));
  body.append(rows.length ? table(HEADERS, rows) : emptyState(COPY.empty));
  const ignored = ignoredBlock(data);
  if (ignored) body.append(ignored);
  body.append(addForm(data));
  return card({title: COPY.logsTitle, body, anchor: 'logs-table'});
}

export function render(data) {
  const found = problems(data);
  const level = found.length ? worst(found.map(item => item.level)) : Level.OK;
  const root = node('div');
  root.append(summaryLine(data, level));
  found.forEach(item => root.append(node('p', item.text, 'warning')));
  root.append(historyCard(), tableCard(data));
  const summary = `${formatBytes(data.totals.bytes)} live, ${formatBytes(data.totals.archived_bytes)} archived`;
  return {node: root, summary, level};
}

export function attentionItems(data) {
  return problems(data).map(item => ({level: item.level, text: item.text, anchor: item.key ? logAnchor(item.key) : 'logs-table'}));
}

export function cardExtra(data) {
  const largest = bySize(data.logs)[0];
  const result = node('p', `${formatBytes(data.totals.bytes)} live`, 'small');
  if (largest) result.append(' · largest ', tag(`${basename(largest.path)} ${formatBytes(largest.bytes)}`));
  return result;
}

export function searchItems(data) {
  const open = (anchorId, label = 'Open') => ({label, run: () => goTo(SECTION, anchorId, {focus: true})});
  const logs = data.logs.map(entry => ({
    title: basename(entry.path), subtitle: `${formatBytes(entry.bytes)} · ${entry.source}`,
    keywords: ['log', 'rotate', entry.path], anchor: logAnchor(entry.path),
    actions: [{label: 'Rotate now', run: () => stageRotate(entry.path)}],
  }));
  return [...logs,
    {title: 'Log rotator agent', subtitle: `com.jacobr.log-rotate · ${rotatorState(data.service)}`, keywords: ['log', 'rotation', 'agent', 'launchd'],
      anchor: 'logs-table', actions: [open('logs-table')]},
    {title: 'Add log path', subtitle: 'Logs', keywords: ['log', 'extra', 'rotate'], anchor: 'add-log', actions: [open('add-log')]}];
}
