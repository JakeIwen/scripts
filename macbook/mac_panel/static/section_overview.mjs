// Overview: built entirely from the state registry, so it costs no requests of its own.
import {card, emptyState, kv, node, timeNode} from './panel_dom.mjs';
import {sections, state} from './panel_sections.mjs';
import {search, settingItems} from './search.mjs';
import {sectionLink} from './shell.mjs';
import {Level, status, worst} from './status_cell.mjs';

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

function quickControls() {
  const body = node('div');
  const metered = state.metered?.payload;
  const restore = state['llm-restore']?.payload;
  const verdict = metered?.fresh ? metered.state.verdict.reason : null;
  const lowData = metered ? (metered.low_data_mode.enabled ? 'On' : 'Off') : null;
  body.append(kv([
    ['Low Data Mode', labelWith(lowData, sectionLink('Open in Metered', 'metered', 'low-data-mode', {focus: true}))],
    ['Network', labelWith(state.metered?.summary ?? null, verdict && node('span', verdict, 'muted'))],
    ['llm-restore snapshots', labelWith(restore ? `${restore.enabled ? 'On' : 'Off'} · ${restore.sessions_open ?? '?'} sessions` : null,
      sectionLink('Open in llm-restore', 'llm-restore', 'snapshots', {focus: true}))],
  ]));
  return card({title: 'Quick controls', body, anchor: 'quick-controls'});
}

function labelWith(text, extra) {
  const row = node('span', null, 'quick-row');
  row.append(text === null ? node('span', 'loading…', 'muted') : document.createTextNode(text));
  if (extra) row.append(' ', extra);
  return row;
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
  root.append(input, results);
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
