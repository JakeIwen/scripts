// A labelled multi-series line chart: y gridlines with values, a time axis, a legend carrying each series'
// current value, end-of-line markers, a tooltip at the nearest time and a note where recording had not begun.
// The layout is pure (Node-tested); lineChart draws it at the container's measured width.
import {appendTimeTicks} from './chart_axes.mjs';
import {html, responsive, svgNode, tooltip} from './chart_dom.mjs';
import {linePoints, pathData, nearestByX} from './charts.mjs';
import {niceTicks, round, spreadLabels, tickLabelWidth, momentLabel, timeToX} from './chart_scale.mjs';
import {absoluteText} from './relative_time.mjs';

const SLACK = 0.01;
const MIN_NOTE_PX = 150;
const PHONE_WIDTH = 480;
const HEIGHTS = {desktop: 180, phone: 140};
const END_DOT = 4;
const TOP_PX = 8;
const UNIT_ROW_PX = 14;
const BOTTOM_PX = 26;
const LABEL_GAP = 13;
const HOVER_SLACK_PX = 8;

export const chartHeight = width => (width < PHONE_WIDTH ? HEIGHTS.phone : HEIGHTS.desktop);

/**
 * When the first sample lies later than `from` (by more than a sliver of the window) the chart has nothing
 * before it: returns that first timestamp, else null. `since` overrides what the data says.
 */
export function recordingStart(series, from, to, since) {
  const first = since ?? Math.min(...series.flatMap(entry => entry.points.filter(point => Number.isFinite(point.value)).map(point => point.ts)));
  if (!Number.isFinite(first)) return null;
  return first > from + (to - from) * SLACK ? Math.min(first, to) : null;
}

/** Everything a draw needs, in pixels: plot box, gridlines, per-series segments and end labels. */
export function lineChartLayout({series, from, to, width, height, yMin, yMax, yFormat = String, maxGap, recordingSince, now = to, hasUnit = false}) {
  const top = TOP_PX + (hasUnit ? UNIT_ROW_PX : 0);
  const values = series.flatMap(entry => entry.points.map(point => point.value)).filter(Number.isFinite);
  const ticks = niceTicks(yMin ?? Math.min(0, ...values), yMax ?? Math.max(...values, 0), 5);
  const ends = series.map(entry => {
    const last = [...entry.points].filter(point => Number.isFinite(point.value) && point.ts <= to && point.ts >= from).pop();
    return last ? yFormat(last.value) : '';
  });
  const left = tickLabelWidth(Math.max(...ticks.ticks.map(value => yFormat(value).length))) + 12;
  const right = Math.max(32, tickLabelWidth(Math.max(...ends.map(text => text.length))) + 14);
  const plot = {left, top, width: Math.max(width - left - right, 40), height: Math.max(height - top - BOTTOM_PX, 40)};
  const placed = series.map(entry => ({
    ...entry,
    ...linePoints(entry.points, {from, to, width: plot.width, height: plot.height, min: ticks.min, max: ticks.max, pad: 0, maxGap}),
  }));
  const since = recordingStart(series, from, to, recordingSince);
  const labelYs = spreadLabels(placed.map(entry => plot.top + (entry.points.length ? entry.points[entry.points.length - 1].y : 0)),
    LABEL_GAP, plot.top, plot.top + plot.height);
  return {
    plot, width, now, from, to, yTicks: ticks.ticks.map(value => ({value, label: yFormat(value),
      y: round(plot.top + (1 - (value - ticks.min) / (ticks.max - ticks.min)) * plot.height)})),
    series: placed.map((entry, index) => ({...entry, endText: ends[index], labelY: labelYs[index],
      last: entry.points.length ? entry.points[entry.points.length - 1] : null})),
    sinceX: since === null ? null : round(timeToX(since, {from, to, width: plot.width})), since,
  };
}

/** The point of each series nearest to plot-relative x; nothing while the cursor is over time before recording began. */
export function valuesAt(layout, x) {
  if (layout.sinceX !== null && x < layout.sinceX - HOVER_SLACK_PX) return layout.series.map(() => null);
  return layout.series.map(entry => nearestByX(entry.points, x));
}

function legend(entries, current) {
  const row = html('div', 'chart-legend line-legend');
  entries.forEach((entry, index) => {
    const item = html('span', 'legend-item');
    item.append(html('span', `swatch c-${entry.color ?? 'accent'}${entry.dashed ? ' dashed' : ''}`), `${entry.label} `,
      html('strong', 'legend-value', current[index]));
    row.append(item);
  });
  return row;
}

function drawGrid(svg, layout) {
  const {plot} = layout;
  layout.yTicks.forEach(tick => {
    svg.append(svgNode('line', {x1: plot.left, x2: plot.left + plot.width, y1: tick.y, y2: tick.y}, 'grid-line'));
    const text = svgNode('text', {x: plot.left - 8, y: tick.y + 4, 'text-anchor': 'end'}, 'axis-label');
    text.textContent = tick.label;
    svg.append(text);
  });
  svg.append(svgNode('line', {x1: plot.left, x2: plot.left + plot.width, y1: plot.top + plot.height + 0.5, y2: plot.top + plot.height + 0.5}, 'axis-line'));
  appendTimeTicks(svg, {from: layout.from, to: layout.to, left: plot.left, width: plot.width, total: layout.width,
    y: plot.top + plot.height});
}

function drawRecordingNote(svg, layout, label, hasData) {
  const {plot} = layout;
  if (layout.sinceX === null && hasData) return;
  const gapWidth = layout.sinceX ?? plot.width;
  svg.append(svgNode('rect', {x: plot.left, y: plot.top, width: round(gapWidth), height: plot.height}, 'recording-gap'));
  if (gapWidth < MIN_NOTE_PX) return;
  const text = svgNode('text', {x: round(plot.left + gapWidth / 2), y: plot.top + plot.height / 2 + 4, 'text-anchor': 'middle'}, 'recording-note');
  text.textContent = label;
  svg.append(text);
}

function drawSeries(svg, layout) {
  const {plot} = layout;
  layout.series.forEach(entry => {
    const cls = `line c-${entry.color ?? 'accent'}${entry.dashed ? ' dashed' : ''}${entry.thin ? ' thin' : ''}`;
    entry.segments.forEach(segment => {
      const moved = segment.map(point => ({x: point.x + plot.left, y: point.y + plot.top}));
      svg.append(svgNode('path', {d: pathData(moved)}, cls));
    });
    if (!entry.last) return;
    const text = svgNode('text', {x: plot.left + plot.width + END_DOT + 6, y: entry.labelY + 4}, `end-label c-${entry.color ?? 'accent'}`);
    text.textContent = entry.endText;
    svg.append(text);
    if (!entry.dashed) {
      svg.append(svgNode('circle', {r: END_DOT, cx: entry.last.x + plot.left, cy: entry.last.y + plot.top}, `end-dot c-${entry.color ?? 'accent'}`));
    }
  });
}

/**
 * series: [{key, label, points: [{ts, value}], dashed?, color?, thin?}] with color one of accent, text, muted;
 * yMin/yMax hint the scale (ticks round outwards); yLabel is the unit shown over the axis; maxGap breaks the
 * line across missing samples; recordingSince overrides the first sample as the start of the data.
 */
export function lineChart({series, from, to, now = to, yMin, yMax, yFormat = String, yLabel, maxGap, recordingSince, noDataText = 'No data in this range'}) {
  const wrap = html('div', 'chart chart-line');
  const lastValues = series.map(entry => {
    const last = entry.points.filter(point => Number.isFinite(point.value) && point.ts <= to).pop();
    return last ? yFormat(last.value) : '–';
  });
  wrap.append(legend(series, lastValues));
  const holder = html('div', 'chart-plot');
  const box = html('div', 'plot-box');
  box.append(holder);
  wrap.append(box);
  const tip = tooltip(box);
  const hasData = series.some(entry => entry.points.some(point => Number.isFinite(point.value)));
  responsive(holder, width => {
    holder.replaceChildren();
    const height = chartHeight(width);
    const layout = lineChartLayout({series, from, to, width, height, yMin, yMax, yFormat, maxGap, recordingSince, now, hasUnit: Boolean(yLabel)});
    const svg = svgNode('svg', {viewBox: `0 0 ${width} ${height}`, width, height, role: 'img',
      'aria-label': `${series.map((entry, index) => `${entry.label} ${lastValues[index]}`).join(', ')}${yLabel ? `, ${yLabel}` : ''}`});
    drawGrid(svg, layout);
    drawRecordingNote(svg, layout, hasData ? `recording since ${layout.since ? momentLabel(layout.since, now) : ''}` : noDataText, hasData);
    drawSeries(svg, layout);
    if (yLabel) {
      const unit = svgNode('text', {x: 0, y: 11}, 'axis-unit');
      unit.textContent = yLabel;
      svg.append(unit);
    }
    const cursor = svgNode('line', {y1: layout.plot.top, y2: layout.plot.top + layout.plot.height}, 'cursor-line');
    cursor.setAttribute('visibility', 'hidden');
    const dots = layout.series.map(entry => {
      const dot = svgNode('circle', {r: END_DOT - 1}, `hover-dot c-${entry.color ?? 'accent'}`);
      dot.setAttribute('visibility', 'hidden');
      return dot;
    });
    const hit = svgNode('rect', {x: layout.plot.left, y: layout.plot.top, width: layout.plot.width, height: layout.plot.height}, 'hit');
    const reset = () => { [cursor, ...dots].forEach(item => item.setAttribute('visibility', 'hidden')); tip.hide(); };
    const showAt = event => {
      const x = Math.min(Math.max(event.clientX - svg.getBoundingClientRect().left - layout.plot.left, 0), layout.plot.width);
      const found = valuesAt(layout, x);
      const anchor = found.filter(Boolean).sort((a, b) => Math.abs(a.x - x) - Math.abs(b.x - x))[0];
      if (!anchor) { reset(); return; }
      cursor.setAttribute('x1', anchor.x + layout.plot.left);
      cursor.setAttribute('x2', anchor.x + layout.plot.left);
      cursor.setAttribute('visibility', 'visible');
      found.forEach((point, index) => {
        dots[index].setAttribute('visibility', point ? 'visible' : 'hidden');
        if (point) { dots[index].setAttribute('cx', point.x + layout.plot.left); dots[index].setAttribute('cy', point.y + layout.plot.top); }
      });
      const lines = layout.series.map((entry, index) => `${entry.label}: ${found[index] ? yFormat(found[index].value) : '–'}`);
      tip.show([absoluteText(anchor.ts * 1000), ...lines], anchor.x + layout.plot.left, true);
    };
    ['pointermove', 'pointerdown'].forEach(name => hit.addEventListener(name, showAt));
    hit.addEventListener('pointerleave', event => { if (event.pointerType === 'mouse') reset(); });
    svg.append(cursor, ...dots, hit);
    holder.append(svg);
  });
  return wrap;
}
