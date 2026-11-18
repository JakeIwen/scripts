// The History card: CPU and memory by kind, and the machine's own memory, swap and CPU, from the sampler's series.
import {historyPanel} from './history_panel.mjs';
import {lineChart} from './line_chart.mjs';
import {card, info, node} from './panel_dom.mjs';
import {RESOURCES_COPY as COPY} from './section_copy.mjs';
import {SECTION} from './resources_actions.mjs';
import {formatBytes, formatPercent} from './resources_format.mjs';

const COPY_HISTORY = COPY.history;
// The sampler's cadence. A gap of ten missed samples breaks the line instead of bridging it, unless the store has
// thinned the series: a seven-day range comes back about 24 minutes apart, so a gap counts as an outage only past
// twice the spacing it reports.
const SAMPLE_SECONDS = 60;
const MISSED_SAMPLES = 10;
const THINNED_GAP_FACTOR = 2;
const HEADROOM = 1.1;
// lineChart offers four colours; the compute line is dashed so it stays apart from the infrastructure one.
const KIND_STYLE = {claude: {color: 'accent'}, codex: {color: 'series-2'}, infra: {color: 'text'}, compute: {color: 'muted', dashed: true}};
const MACHINE_STYLE = {'system.memory_used': {color: 'accent'}, 'system.swap_used': {color: 'series-2'}};
const CHARTS = [
  {id: 'cpu', keys: Object.keys(KIND_STYLE).map(kind => `${kind}.cpu`), styles: kind => KIND_STYLE[kind.split('.')[0]], format: value => formatPercent(value)},
  {id: 'memory', keys: Object.keys(KIND_STYLE).map(kind => `${kind}.rss`), styles: kind => KIND_STYLE[kind.split('.')[0]], format: formatBytes},
  {id: 'machine', keys: Object.keys(MACHINE_STYLE), styles: key => MACHINE_STYLE[key], format: formatBytes},
  {id: 'load', keys: ['system.cpu'], styles: () => ({color: 'accent'}), format: value => formatPercent(value)},
];

const seriesLabel = key => COPY_HISTORY.series[key] ?? COPY_HISTORY.series[key.split('.')[0]] ?? key;

/** Series with at least one real value for a chart, as lineChart wants them (nulls dropped, never plotted as zero). */
export function chartSeries(spec, series) {
  return spec.keys.map(key => ({key, label: seriesLabel(key), ...spec.styles(key),
    points: (series[key] ?? []).filter(point => point.value !== null && point.value !== undefined)}))
    .filter(entry => entry.points.length);
}

/** Longest gap a chart bridges: the largest spacing the store reported for any of its series sets it, never below the cadence rule. */
export function chartMaxGap(spec, spacing) {
  const widest = Math.max(0, ...spec.keys.map(key => Number(spacing?.[key]) || 0));
  return Math.max(MISSED_SAMPLES * SAMPLE_SECONDS, THINNED_GAP_FACTOR * widest);
}

function drawHistory(series, window, spacing) {
  return CHARTS.flatMap(spec => {
    const lines = chartSeries(spec, series ?? {});
    if (!lines.length) return [];
    const peak = Math.max(...lines.flatMap(entry => entry.points.map(point => point.value)));
    const copy = COPY_HISTORY.charts[spec.id];
    return [node('h4', copy.title), lineChart({series: lines, ...window, yMin: 0, yMax: Math.max(peak * HEADROOM, 1), yFormat: spec.format,
      yLabel: copy.unit, maxGap: chartMaxGap(spec, spacing)})];
  });
}

export function historyCard() {
  const body = historyPanel({id: SECTION, section: SECTION, draw: drawHistory, emptyText: COPY_HISTORY.noHistory});
  return card({title: COPY_HISTORY.title, body, anchor: 'history', actions: info(COPY_HISTORY.info)});
}
