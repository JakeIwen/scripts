// Overview: built entirely from the state registry, so it costs no requests of its own.
import {api} from './panel_api.mjs';
import {card, emptyState, node, notice, settingRow, timeNode, toggle} from './panel_dom.mjs';
import {refresh, sections, state} from './panel_sections.mjs';
import {search, settingItems} from './search.mjs';
import {JOBS_COPY} from './section_copy.mjs';
import {sectionLink} from './shell.mjs';
import {Level, status, worst} from './status_cell.mjs';

const COPY = JOBS_COPY.overview;
const FINDER_RESULTS = 6;
const SEVERITY = {[Level.ERROR]: 0, [Level.WARN]: 1};

// A section may export attentionItems(payload) -> [{level, text, anchor?}] to name the exact rows;
// without it (or when it finds none) the section's own summary line stands in.
function attentionRows() {
  const rows = [];
  sections().filter(entry => entry.id !== 'overview').forEach(entry => {
    const current = state[entry.id];
    if (!current) return;
    const items = entry.attentionItems && current.payload ? entry.attentionItems(current.payload) : [];
    if (current.error) {
      rows.push({id: entry.id, level: Level.ERROR, text: `Could not load: ${current.error}`});
    } else if (items.length) {
      items.forEach(item => rows.push({id: entry.id, ...item}));
    } else if (current.level in SEVERITY) {
      rows.push({id: entry.id, level: current.level, text: current.summary});
    }
  });
  return rows.sort((a, b) => SEVERITY[a.level] - SEVERITY[b.level]);
}

function attentionCard(rows, entries) {
  const body = node('div');
  if (!rows.length) {
    const times = entries.map(entry => state[entry.id]?.updatedAt).filter(Boolean);
    const line = node('p', 'Everything is healthy', 'overview-ok');
    if (times.length) line.append(' · checked ', timeNode(Math.min(...times) / 1000));
    body.append(line);
  } else {
    const list = node('ul', null, 'attention');
    rows.forEach(row => {
      const item = node('li');
      item.append(status(row.level, `${titleOf(row.id)}: ${row.text}`), ' ', sectionLink('Open', row.id, row.anchor));
      list.append(item);
    });
    body.append(list);
  }
  return card({title: 'Needs attention', level: rows.length ? rows[0].level : Level.OK, body, anchor: 'attention'});
}

const titleOf = id => sections().find(entry => entry.id === id)?.title ?? id;

// A switch changes one tool and then reloads the sections that show it, so every view agrees at once.
async function flip(path, body, reload) {
  try {
    const result = await api(path, body);
    notice('overview', result.message || 'Done', 'ok');
  } catch (error) {
    notice('overview', error.message, 'error');
  } finally {
    await Promise.all(reload.map(id => refresh(id)));
  }
}

const loadingRow = label => settingRow({label, control: node('span', 'loading…', 'muted')});

function lowDataRow() {
  const metered = state.metered?.payload;
  if (!metered) return loadingRow('Low Data Mode');
  const {enabled, revision, error} = metered.low_data_mode;
  const control = node('div', null, 'actions');
  control.append(toggle({
    label: enabled ? 'On' : 'Off', on: enabled, confirm: true, key: 'overview-low-data',
    onChange: next => flip('/api/metered', {action: 'low-data-mode', enabled: next, revision, confirmed: true}, ['metered']),
  }));
  if (error) control.append(node('span', error, 'error small'));
  return settingRow({label: 'Low Data Mode', info: COPY.lowDataInfo, control, anchor: 'low-data'});
}

function restoreRow() {
  const restore = state['llm-restore']?.payload;
  if (!restore) return loadingRow('llm-restore snapshots');
  const control = node('div', null, 'actions');
  control.append(toggle({
    label: restore.enabled ? 'On' : 'Off', on: restore.enabled, key: 'overview-restore',
    onChange: next => flip('/api/llm-restore', {enabled: next}, ['llm-restore', 'crontab']),
  }), node('span', `${restore.sessions_open ?? '?'} sessions open`, 'muted small'));
  return settingRow({label: 'llm-restore snapshots', info: COPY.restoreInfo, control});
}

function quickControls() {
  const body = node('div');
  const metered = state.metered?.payload;
  const verdict = metered?.fresh ? metered.state.verdict.reason : null;
  const network = node('span');
  network.append(state.metered?.summary ?? 'loading…', verdict ? ' · ' : '', verdict ? node('span', verdict, 'muted') : '',
    ' ', sectionLink('Open in Metered', 'metered'));
  body.append(lowDataRow(), settingRow({label: 'Network', control: network}), restoreRow());
  return card({title: COPY.quickTitle, body, anchor: 'quick-controls'});
}

function sectionCard(entry) {
  const current = state[entry.id];
  const body = node('div');
  body.append(node('p', current?.summary || 'Loading…', 'muted'));
  const extra = current?.payload && entry.cardExtra ? entry.cardExtra(current.payload) : null;
  if (extra) body.append(extra);
  const footer = node('p', null, 'small');
  footer.append(current?.updatedAt ? timeNode(current.updatedAt / 1000) : '', current?.updatedAt ? ' · ' : '',
    sectionLink('Open', entry.id));
  body.append(footer);
  return card({title: sectionLink(entry.title, entry.id), level: current?.level ?? Level.UNKNOWN, body});
}

// Kept across renders so a poll never throws away what was typed; defaultValue follows the value so
// the poll's unsaved-edits check does not hold Overview back.
let finder = null;

function settingsFinder() {
  if (finder) return finder;
  const root = node('div', null, 'settings-finder');
  const input = node('input');
  input.type = 'search';
  input.placeholder = 'Find a setting';
  input.setAttribute('aria-label', 'Find a setting');
  input.dataset.key = 'settings-finder';
  const results = node('ul', null, 'finder-results');
  input.addEventListener('input', () => {
    input.defaultValue = input.value;
    const found = search(settingItems(state.settings?.payload), input.value, FINDER_RESULTS);
    results.replaceChildren(...found.map(item => {
      const li = node('li');
      li.append(sectionLink(item.title, 'settings', item.anchor, {focus: true}));
      return li;
    }));
    if (input.value.trim() && !found.length) results.replaceChildren(emptyState('No matching setting'));
  });
  root.append(input, results, sectionLink(COPY.allSettings, 'settings'));
  finder = root;
  return root;
}

export function render() {
  const entries = sections().filter(entry => entry.id !== 'overview');
  const rows = attentionRows();
  const root = node('div', null, 'overview');
  root.append(attentionCard(rows, entries), quickControls());
  const settings = card({title: 'Settings', body: settingsFinder(), anchor: 'settings-finder'});
  root.append(settings);
  const grid = node('div', null, 'card-grid');
  entries.forEach(entry => grid.append(sectionCard(entry)));
  root.append(grid);
  const levels = entries.map(entry => state[entry.id]?.level).filter(Boolean);
  const level = levels.length ? worst(levels.filter(item => item in SEVERITY)) : Level.UNKNOWN;
  const summary = rows.length ? `${rows.length} need${rows.length === 1 ? 's' : ''} attention` : 'all healthy';
  return {node: root, summary, level};
}
