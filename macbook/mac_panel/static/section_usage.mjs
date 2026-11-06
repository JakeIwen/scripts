// Usage: allowance left in each window as gauges against the even-spend line, the routing the usage-budget
// script chose, and the sampled history. Shows only what the sanitized facts carry; no account identity.
import {gauge, sparkline} from './charts.mjs';
import {historyPanel} from './history_panel.mjs';
import {card, emptyState, info, kv, node, tag, timeNode} from './panel_dom.mjs';
import {USAGE_COPY as COPY} from './section_copy.mjs';
import {goTo} from './shell.mjs';
import {Level, status, worst} from './status_cell.mjs';

const SECTION = 'usage';
const PERCENT = 100;
const LOW_PERCENT = 10;
const MINUTES_PER_HOUR = 60;
const HISTORY_MAX_GAP = 3600;
const percent = value => `${Math.round(value)}%`;
const AVAILABILITY_LEVEL = {ok: Level.OK, constrained: Level.WARN, out: Level.ERROR, unknown: Level.UNKNOWN};
const FAMILY = {claude: 'Claude', gpt: 'Codex'};
// Routing text keys the usage tool has sent; any other string key still shows under its own name.
const ROUTE_LABELS = {workers: 'Workers', builders: 'Builders', review: 'Review', parents: 'Parents'};
const lineLeft = (data, spec) => (spec.position ? data[spec.position]?.line_left : undefined);

// One entry per allowance window; `key` is the facts field and, with `_left`, the history series.
const WINDOWS = [
  {key: 'claude_weekly', label: 'Claude, this week', position: 'claude_position'},
  {key: 'claude_session', label: 'Claude, five-hour window'},
  {key: 'fable_weekly', label: 'Fable, this week'},
  {key: 'codex_weekly', label: 'Codex, this week', position: 'gpt_position'},
  {key: 'codex_session', label: 'Codex, session window'},
];

function windowGauge(spec, data) {
  const window = data[spec.key];
  if (!window) return null;
  const marker = lineLeft(data, spec);
  const result = node('div');
  result.append(gauge({value: window.left, max: PERCENT, marker, label: spec.label, format: percent}));
  if (window.resets_at) {
    const caption = node('p', 'Resets ', 'muted small');
    caption.append(timeNode(window.resets_at));
    result.append(caption);
  }
  return result;
}

// Credits have no fixed size, so the gauge compares them with what the daily allowance would use to expiry.
function creditsGauge(credits) {
  const wanted = (credits?.allowance_per_day ?? 0) * (credits?.days_left ?? 0);
  if (credits?.left === undefined || !(wanted > 0)) return null;
  return gauge({value: Math.min(credits.left, wanted), max: wanted, label: 'Credits to expiry',
    format: value => String(Math.round(value))});
}

function creditsRows(credits) {
  const number = value => (value == null ? undefined : String(Math.round(value * 10) / 10));
  const days = credits.days_left == null ? '' : ` (${Math.round(credits.days_left)} days)`;
  return kv([
    ['Credits left', number(credits.left)],
    ['Allowance per day', number(credits.allowance_per_day)],
    ['Burn per day', number(credits.burn_per_day)],
    ['Expires', credits.expires ? `${credits.expires}${days}` : undefined],
  ]);
}

function titled(text, help) {
  const title = node('span', text);
  title.append(info(help));
  return title;
}

function budgetsCard(data) {
  const body = node('div');
  WINDOWS.forEach(spec => {
    const drawn = windowGauge(spec, data);
    if (drawn) body.append(drawn);
  });
  if (!body.childElementCount) body.append(emptyState(COPY.noData));
  if (data.credits) {
    const credits = creditsGauge(data.credits);
    if (credits) body.append(credits);
    body.append(creditsRows(data.credits), node('p', COPY.creditsInfo, 'muted small'));
  }
  return card({title: titled(COPY.budgetsTitle, COPY.budgetsInfo), body, anchor: 'budgets'});
}

function withInfo(content, help) {
  const result = node('span');
  result.append(content, info(help));
  return result;
}

function routingCard(data) {
  const body = node('div');
  const availability = node('span', null, 'actions');
  Object.entries(data.availability).forEach(([family, state]) => {
    availability.append(tag(`${FAMILY[family] ?? family}: ${state}`, AVAILABILITY_LEVEL[state]));
  });
  const route = Object.entries(data.route ?? {}).map(([key, text]) => [ROUTE_LABELS[key] ?? key, text]);
  body.append(kv([
    ['Lean', data.lean ? tag(data.lean) : undefined],
    ...route,
    ['Availability', withInfo(availability, 'out or constrained means that provider gets no new workers until its window resets.')],
    ['Mode source', data.mode_source],
    ['Expires', data.expires_at],
    ['Snapshot', snapshotAge(data)],
  ]));
  const notes = [...(data.lines ?? []), ...(data.reasons ?? [])];
  if (notes.length) {
    const list = node('ul', null, 'findings');
    notes.forEach(text => list.append(node('li', text)));
    body.append(list);
  }
  return card({title: titled(COPY.routingTitle, COPY.routingInfo), body, anchor: 'routing'});
}

function snapshotAge(data) {
  const result = node('span');
  result.append(timeNode(data.fetched_at), ` (${Math.round(data.age_minutes)} min old)`);
  return result;
}

// ---- history ----

const SERIES = [
  ...WINDOWS.map(spec => ({key: `${spec.key}_left`, label: spec.label, format: percent, min: 0, max: PERCENT})),
  {key: 'claude_line_left', label: 'Claude even-spend line', format: percent, min: 0, max: PERCENT},
  {key: 'credits', label: 'Credits left', format: value => String(Math.round(value))},
];

function drawHistory(series, window) {
  const charts = [];
  SERIES.forEach(spec => {
    const points = (series[spec.key] ?? []).filter(point => point.value !== null);
    if (!points.length) return;
    charts.push(node('h4', spec.label), sparkline({points, ...window, min: spec.min, max: spec.max,
      format: spec.format, maxGap: HISTORY_MAX_GAP}));
  });
  return charts;
}

function historyCard() {
  const body = historyPanel({id: SECTION, section: 'usage', draw: drawHistory, emptyText: COPY.noHistory});
  return card({title: titled(COPY.historyTitle, COPY.historyInfo), body, anchor: 'history'});
}

// ---- section contract ----

function assess(data) {
  const levels = Object.values(data.availability).map(state => AVAILABILITY_LEVEL[state]);
  if (data.age_minutes > COPY.staleMinutes) levels.push(Level.WARN);
  const low = WINDOWS.some(spec => data[spec.key] && data[spec.key].left < LOW_PERCENT);
  if (low) levels.push(Level.WARN);
  return worst(levels);
}

export function render(data) {
  const root = node('div');
  const level = assess(data);
  const claude = data.claude_weekly ? `Claude ${percent(data.claude_weekly.left)}` : 'Claude n/a';
  const codex = data.codex_weekly ? `Codex ${percent(data.codex_weekly.left)}` : 'Codex n/a';
  const summary = [claude, codex, data.lean].filter(Boolean).join(' · ');
  const line = node('p', null, 'summary-line');
  line.append(status(level, summary));
  root.append(line);
  root.append(budgetsCard(data), routingCard(data), historyCard());
  return {node: root, summary, level};
}

export function attentionItems(data) {
  const items = [];
  Object.entries(data.availability).forEach(([family, state]) => {
    if (state === 'out') items.push({level: Level.ERROR, text: `${FAMILY[family] ?? family} gets no new workers until its window resets`, anchor: 'routing'});
    else if (state === 'constrained') items.push({level: Level.WARN, text: `${FAMILY[family] ?? family} allowance is constrained`, anchor: 'routing'});
  });
  WINDOWS.forEach(spec => {
    const window = data[spec.key];
    if (window && window.left < LOW_PERCENT) items.push({level: Level.WARN, text: `${spec.label}: ${percent(window.left)} left`, anchor: 'budgets'});
  });
  if (data.age_minutes > COPY.staleMinutes) {
    items.push({level: Level.WARN, text: `Usage snapshot is ${Math.round(data.age_minutes / MINUTES_PER_HOUR)} h old`, anchor: 'routing'});
  }
  return items;
}

// Overview's card shows the two headline windows small.
export function cardExtra(data) {
  const result = node('div');
  WINDOWS.filter(spec => ['claude_weekly', 'codex_weekly'].includes(spec.key) && data[spec.key]).forEach(spec => {
    result.append(gauge({value: data[spec.key].left, max: PERCENT, label: spec.label, format: percent,
      marker: lineLeft(data, spec)}));
  });
  return result.childElementCount ? result : null;
}

export function searchItems() {
  const open = (anchorId, label) => ({label, run: () => goTo(SECTION, anchorId)});
  return [
    {title: 'Usage budgets', subtitle: 'Allowance left in each window', keywords: ['claude', 'codex', 'fable', 'credits', 'gauge'],
      anchor: 'budgets', actions: [open('budgets', 'Open')]},
    {title: 'Usage routing', subtitle: 'Lean, workers and review', keywords: ['lean', 'availability', 'workers'],
      anchor: 'routing', actions: [open('routing', 'Open')]},
  ];
}
