// Inline-SVG charts. The geometry functions are pure (tested under Node); the renderers draw them at the
// element's measured pixel width so strokes stay 2 px and markers stay round. Colours come from classes
// in charts.css, which read the tokens. Times are epoch seconds, as the history API returns them.
import {absoluteText} from './relative_time.mjs';

const SVG_NS = 'http://www.w3.org/2000/svg';
export const RANGES = Object.freeze({'24h': 86400, '7d': 7 * 86400});
const DEFAULT_WIDTH = 320;
const MIN_BAND_PX = 1.5;
const TIP_HIT_PX = 24;
const MARKER_RADIUS = 4;
const MIN_STATE_GAP = 5 * 60;

export const stateGap = spacing => Math.max(MIN_STATE_GAP, 3 * (spacing ?? 0));

const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
const round = value => Math.round(value * 10) / 10;

// ---- geometry (pure) ----

export function rangeWindow(range, now) {
  if (!RANGES[range]) throw new RangeError(`Unknown range ${range}`);
  return {from: now - RANGES[range], to: now};
}

export const timeToX = (ts, {from, to, width}) => clamp((ts - from) / (to - from), 0, 1) * width;
export const xToTime = (x, {from, to, width}) => from + clamp(x / width, 0, 1) * (to - from);

// Taller bands for worse states, so severity is not carried by colour alone.
export const BAND_HEIGHT = Object.freeze({error: 1, warn: 0.8, unknown: 0.55, ok: 0.6, idle: 0.6, nodata: 0.35});

/**
 * samples [{ts, level, text?}] sorted by time; each holds until the next sample. A sample older than
 * `maxGap` seconds with nothing after it ends there and the rest shows as `nodata`, never as the old state.
 */
export function stateBands(samples, {from, to, width, maxGap = Infinity}) {
  const bands = [];
  const push = (start, end, level, text, value) => {
    const s = Math.max(start, from);
    const e = Math.min(end, to);
    if (e <= s) return;
    const last = bands[bands.length - 1];
    if (last && last.level === level && last.text === text && last.end === s) {
      last.end = e;
      return;
    }
    bands.push({start: s, end: e, level, text, value});
  };
  const sorted = samples.filter(sample => sample.ts < to);
  if (!sorted.length || sorted[0].ts > from) push(from, sorted.length ? sorted[0].ts : to, 'nodata', 'no data');
  sorted.forEach((sample, index) => {
    const next = index + 1 < sorted.length ? sorted[index + 1].ts : to;
    const held = Math.min(next, sample.ts + maxGap);
    push(sample.ts, held, sample.level, sample.text ?? sample.level, sample.value);
    if (held < next) push(held, next, 'nodata', 'no data');
  });
  const scale = {from, to, width};
  return bands.map(band => {
    const x = timeToX(band.start, scale);
    return {...band, x, width: Math.max(timeToX(band.end, scale) - x, MIN_BAND_PX)};
  });
}

/** Points with a finite value inside the window, as pixel coordinates; a gap above `maxGap` breaks the line. */
export function linePoints(points, {from, to, width, height, min, max, pad = MARKER_RADIUS, maxGap = Infinity}) {
  const kept = points.filter(point => Number.isFinite(point.value) && point.ts >= from && point.ts <= to)
    .sort((a, b) => a.ts - b.ts);
  const values = kept.map(point => point.value);
  let low = min ?? Math.min(...values);
  let high = max ?? Math.max(...values);
  if (!Number.isFinite(low) || !Number.isFinite(high)) [low, high] = [0, 1];
  if (high === low) [low, high] = [low - 1, high + 1];
  const placed = kept.map(point => ({
    ...point,
    x: round(timeToX(point.ts, {from, to, width})),
    y: round(pad + (1 - (point.value - low) / (high - low)) * (height - 2 * pad)),
  }));
  const segments = [];
  placed.forEach((point, index) => {
    if (index === 0 || point.ts - placed[index - 1].ts > maxGap) segments.push([]);
    segments[segments.length - 1].push(point);
  });
  return {points: placed, segments, min: low, max: high};
}

export const pathData = segment => segment.map((point, index) => `${index ? 'L' : 'M'}${point.x} ${point.y}`).join(' ');

export function nearestByX(points, x) {
  let best = null;
  points.forEach(point => { if (best === null || Math.abs(point.x - x) < Math.abs(best.x - x)) best = point; });
  return best;
}

/** Gauge of an amount left: fill fraction, spend-line position and a severity level. */
export function gaugeGeometry({value, max, marker}, width) {
  const fraction = max > 0 ? clamp(value / max, 0, 1) : 0;
  const markerFraction = marker === undefined || marker === null || !(max > 0) ? null : clamp(marker / max, 0, 1);
  let level = 'ok';
  if (fraction < 0.15) level = 'error';
  else if (fraction < 0.3 || (markerFraction !== null && fraction < markerFraction)) level = 'warn';
  return {fraction, fill: round(fraction * width), markerX: markerFraction === null ? null : round(markerFraction * width), level};
}

/** Events [{ts, lane, label}] laid out one row per lane in first-seen order (or `laneOrder`). */
export function scheduleLayout(events, {from, to, width, now, laneOrder}) {
  const lanes = new Map((laneOrder ?? []).map(lane => [lane, []]));
  events.filter(event => event.ts >= from && event.ts <= to).sort((a, b) => a.ts - b.ts).forEach(event => {
    if (!lanes.has(event.lane)) lanes.set(event.lane, []);
    lanes.get(event.lane).push({...event, x: round(timeToX(event.ts, {from, to, width})), future: event.ts > now});
  });
  return {
    lanes: [...lanes].map(([lane, ticks]) => ({lane, ticks})),
    nowX: now >= from && now <= to ? round(timeToX(now, {from, to, width})) : null,
  };
}

export const spanText = seconds => {
  if (seconds < 90) return `${Math.round(seconds)} s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} min`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)} h`;
  return `${Math.round(seconds / 86400)} d`;
};

// ---- DOM helpers ----

function svgNode(tag, attributes = {}, className) {
  const element = document.createElementNS(SVG_NS, tag);
  Object.entries(attributes).forEach(([name, value]) => element.setAttribute(name, value));
  if (className) element.setAttribute('class', className);
  return element;
}

function html(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

// Redraws at the measured width; the first draw uses a default so a detached chart still has content.
function responsive(wrap, draw) {
  let last = 0;
  const run = width => {
    const rounded = Math.round(width);
    if (rounded < 40 || rounded === last) return;
    last = rounded;
    draw(rounded);
  };
  run(DEFAULT_WIDTH);
  if (typeof ResizeObserver !== 'undefined') new ResizeObserver(([entry]) => run(entry.contentRect.width)).observe(wrap);
}

const activeTips = new Set();
let tipDismissal = false;

// One tooltip per chart: value first, then the time or detail; hover for a mouse, click or tap for touch.
function tooltip(wrap) {
  if (!tipDismissal) {
    tipDismissal = true;
    document.addEventListener('pointerdown', event => {
      activeTips.forEach(tip => { if (!tip.wrap.contains(event.target)) tip.hide(); });
    });
  }
  const box = html('div', 'chart-tip');
  box.hidden = true;
  box.setAttribute('role', 'status');
  wrap.append(box);
  const tip = {
    wrap,
    show(lines, x) {
      box.replaceChildren(...lines.map((line, index) => html('div', index ? 'chart-tip-detail' : 'chart-tip-value', line)));
      box.hidden = false;
      box.style.setProperty('--tip-left', `${clamp(x - box.offsetWidth / 2, 0, Math.max(0, wrap.clientWidth - box.offsetWidth))}px`);
      activeTips.add(tip);
    },
    hide() {
      box.hidden = true;
      activeTips.delete(tip);
    },
  };
  return tip;
}

// Mouse hover shows and leaving hides; touch and click show and stay until a tap elsewhere.
function bindTip(target, tip, content) {
  const show = event => tip.show(content(), event.offsetX ?? 0);
  target.addEventListener('pointerenter', show);
  target.addEventListener('pointermove', show);
  target.addEventListener('pointerdown', show);
  target.addEventListener('pointerleave', event => { if (event.pointerType === 'mouse') tip.hide(); });
}

const levelWord = level => ({nodata: 'no data', ok: 'ok', idle: 'idle', unknown: 'unknown', warn: 'needs attention', error: 'failing'}[level] ?? level);
const timeSpan = (start, end) => `${absoluteText(start * 1000)} to ${absoluteText(end * 1000)}`;

// ---- renderers ----

/** Coloured bands over time. samples: [{ts, level, text?}]. */
export function stateStrip({samples, from, to, height = 22, spacing, maxGap = stateGap(spacing)}) {
  const wrap = html('div', 'chart chart-strip');
  const holder = html('div', 'chart-plot');
  wrap.append(holder);
  const tip = tooltip(wrap);
  const legend = html('div', 'chart-legend');
  const present = new Set();
  responsive(holder, width => {
    holder.replaceChildren();
    const svg = svgNode('svg', {viewBox: `0 0 ${width} ${height}`, width, height, role: 'img',
      'aria-label': `State history, ${absoluteText(from * 1000)} to ${absoluteText(to * 1000)}`});
    stateBands(samples, {from, to, width, maxGap}).forEach(band => {
      present.add(band.level);
      const barHeight = round((BAND_HEIGHT[band.level] ?? 0.6) * height);
      const rect = svgNode('rect', {x: round(band.x), y: height - barHeight, width: round(band.width), height: barHeight,
        tabindex: 0, 'aria-label': `${band.text}, ${timeSpan(band.start, band.end)}`}, `band level-${band.level}`);
      const content = () => [`${band.text}`, `${timeSpan(band.start, band.end)} (${spanText(band.end - band.start)})`];
      bindTip(rect, tip, content);
      rect.addEventListener('focus', () => tip.show(content(), band.x + band.width / 2));
      rect.addEventListener('blur', () => tip.hide());
      svg.append(rect);
    });
    holder.append(svg);
    legend.replaceChildren(...[...present].map(level => {
      const item = html('span', 'legend-item');
      item.append(html('span', `dot level-${level}`), levelWord(level));
      return item;
    }));
  });
  wrap.append(legend);
  return wrap;
}

/** Line of values over time. points: [{ts, value}]; min/max fix the scale (e.g. 0 and 100). */
export function sparkline({points, from, to, min, max, height = 40, format = String, maxGap}) {
  const wrap = html('div', 'chart chart-spark');
  const holder = html('div', 'chart-plot');
  wrap.append(holder);
  const tip = tooltip(wrap);
  responsive(holder, width => {
    holder.replaceChildren();
    const plot = linePoints(points, {from, to, width, height, min, max, maxGap});
    const svg = svgNode('svg', {viewBox: `0 0 ${width} ${height}`, width, height, role: 'img', tabindex: 0,
      'aria-label': plot.points.length ? `Line chart, latest ${format(plot.points[plot.points.length - 1].value)}` : 'No data'});
    plot.segments.forEach(segment => svg.append(svgNode('path', {d: pathData(segment)}, 'spark-line')));
    const last = plot.points[plot.points.length - 1];
    const dot = svgNode('circle', {r: MARKER_RADIUS, cx: last?.x ?? 0, cy: last?.y ?? 0}, 'spark-dot');
    dot.hidden = !last;
    svg.append(dot);
    const hit = svgNode('rect', {x: 0, y: 0, width, height}, 'hit');
    const showNearest = event => {
      const point = nearestByX(plot.points, event.offsetX);
      if (!point) return;
      dot.setAttribute('cx', point.x);
      dot.setAttribute('cy', point.y);
      tip.show([format(point.value), absoluteText(point.ts * 1000)], point.x);
    };
    ['pointermove', 'pointerdown'].forEach(name => hit.addEventListener(name, showNearest));
    hit.addEventListener('pointerleave', event => {
      if (event.pointerType !== 'mouse') return;
      tip.hide();
      if (last) { dot.setAttribute('cx', last.x); dot.setAttribute('cy', last.y); }
    });
    svg.append(hit);
    svg.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key) || !plot.points.length) return;
      event.preventDefault();
      const current = Number(svg.dataset.index ?? plot.points.length - 1);
      const next = clamp(current + (event.key === 'ArrowRight' ? 1 : -1), 0, plot.points.length - 1);
      svg.dataset.index = next;
      const point = plot.points[next];
      dot.setAttribute('cx', point.x);
      dot.setAttribute('cy', point.y);
      tip.show([format(point.value), absoluteText(point.ts * 1000)], point.x);
    });
    holder.append(svg);
  });
  return wrap;
}

/** Horizontal meter of an amount left, with an optional spend-line marker. */
export function gauge({value, max, marker, label, format = String, markerLabel = 'even spend'}) {
  const wrap = html('div', 'chart chart-gauge');
  const head = html('div', 'gauge-head');
  head.append(html('span', 'gauge-label', label), html('span', 'gauge-value', `${format(value)} of ${format(max)}`));
  const holder = html('div', 'chart-plot');
  wrap.append(head, holder);
  const tip = tooltip(wrap);
  const meta = gaugeGeometry({value, max, marker}, 1);
  wrap.dataset.level = meta.level;
  responsive(holder, width => {
    holder.replaceChildren();
    const geometry = gaugeGeometry({value, max, marker}, width);
    const svg = svgNode('svg', {viewBox: `0 0 ${width} 14`, width, height: 14, role: 'meter',
      'aria-valuemin': 0, 'aria-valuemax': max, 'aria-valuenow': value, 'aria-label': label});
    svg.append(svgNode('rect', {x: 0, y: 3, width, height: 8, rx: 4}, 'gauge-track'),
      svgNode('rect', {x: 0, y: 3, width: geometry.fill, height: 8, rx: 4}, `gauge-fill level-${geometry.level}`));
    if (geometry.markerX !== null) {
      svg.append(svgNode('rect', {x: clamp(geometry.markerX - 1, 0, width - 2), y: 0, width: 2, height: 14}, 'gauge-marker'));
    }
    const lines = () => [`${format(value)} of ${format(max)}`,
      geometry.markerX === null ? label : `${markerLabel}: ${format(marker)}`];
    bindTip(svg, tip, lines);
    holder.append(svg);
  });
  return wrap;
}

/** Ticks per event, one row per lane. events: [{ts, lane, label}]. */
export function scheduleStrip({events, from, to, now, laneOrder}) {
  const wrap = html('div', 'chart chart-schedule');
  const holder = html('div', 'chart-plot');
  wrap.append(holder);
  const tip = tooltip(wrap);
  const ROW = 20;
  const LABEL_PX = 110;
  responsive(holder, width => {
    holder.replaceChildren();
    const plotWidth = Math.max(width - LABEL_PX, 40);
    const layout = scheduleLayout(events, {from, to, width: plotWidth, now, laneOrder});
    const height = Math.max(layout.lanes.length, 1) * ROW;
    const svg = svgNode('svg', {viewBox: `0 0 ${width} ${height}`, width, height, role: 'img',
      'aria-label': `Schedule: ${events.length} runs in view`});
    layout.lanes.forEach((lane, row) => {
      const y = row * ROW;
      const text = svgNode('text', {x: 0, y: y + ROW / 2 + 4}, 'lane-label');
      text.textContent = lane.lane.length > 16 ? `${lane.lane.slice(0, 15)}…` : lane.lane;
      svg.append(text, svgNode('line', {x1: LABEL_PX, x2: width, y1: y + ROW - 0.5, y2: y + ROW - 0.5}, 'lane-rule'));
      lane.ticks.forEach(tick => {
        const x = LABEL_PX + tick.x;
        const mark = svgNode('rect', {x: round(x - 1), y: y + 3, width: 2, height: ROW - 7}, `tick${tick.future ? ' future' : ''}`);
        const hit = svgNode('rect', {x: round(x - TIP_HIT_PX / 2), y, width: TIP_HIT_PX, height: ROW}, 'hit');
        bindTip(hit, tip, () => [tick.label ?? lane.lane, `${tick.future ? 'due' : 'ran'} ${absoluteText(tick.ts * 1000)}`]);
        svg.append(mark, hit);
      });
    });
    if (layout.nowX !== null) {
      svg.append(svgNode('line', {x1: LABEL_PX + layout.nowX, x2: LABEL_PX + layout.nowX, y1: 0, y2: height}, 'now-line'));
    }
    holder.append(svg);
  });
  return wrap;
}

/** The 24 h / 7 d choice every history chart shares; calls onChange(range) and marks the active one. */
export function rangeToggle({ranges = Object.keys(RANGES), value, onChange}) {
  const group = html('div', 'range-toggle');
  group.setAttribute('role', 'group');
  group.setAttribute('aria-label', 'Time range');
  ranges.forEach(range => {
    const choice = html('button', 'range-choice', range === '24h' ? '24 h' : range === '7d' ? '7 d' : range);
    choice.type = 'button';
    choice.setAttribute('aria-pressed', String(range === value));
    choice.addEventListener('click', () => {
      group.querySelectorAll('button').forEach(other => other.setAttribute('aria-pressed', String(other === choice)));
      onChange(range);
    });
    group.append(choice);
  });
  return group;
}
