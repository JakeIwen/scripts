// The History card: CPU and memory by kind, and the machine's own memory, swap and CPU, from the sampler's series.
import {historyPanel} from './history_panel.mjs';
import {lineChart} from './line_chart.mjs';
import {card, info, node} from './panel_dom.mjs';
import {RESOURCES_COPY as COPY} from './section_copy.mjs';
import {SECTION} from './resources_actions.mjs';
import {formatBytes, formatPercent} from './resources_format.mjs';

const COPY_HISTORY = COPY.history;
// A gap longer than a few missed one-minute samples breaks the line instead of bridging it.
const MAX_GAP_SECONDS = 600;
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

function drawHistory(series, window) {
  return CHARTS.flatMap(spec => {
    const lines = chartSeries(spec, series ?? {});
    if (!lines.length) return [];
    const peak = Math.max(...lines.flatMap(entry => entry.points.map(point => point.value)));
    const copy = COPY_HISTORY.charts[spec.id];
    return [node('h4', copy.title), lineChart({series: lines, ...window, yMin: 0, yMax: Math.max(peak * HEADROOM, 1), yFormat: spec.format,
      yLabel: copy.unit, maxGap: MAX_GAP_SECONDS})];
  });
}

export function historyCard() {
  const body = historyPanel({id: SECTION, section: SECTION, draw: drawHistory, emptyText: COPY_HISTORY.noHistory});
  return card({title: COPY_HISTORY.title, body, anchor: 'history', actions: info(COPY_HISTORY.info)});
}
