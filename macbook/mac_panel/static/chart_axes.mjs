// Shared axis furniture: the time axis row under a strip (or a group of strips) and the legend of state
// colours, so no band is ever unlabelled. lineChart draws its own axes through `appendTimeTicks`.
import {html, responsive, svgNode} from './chart_dom.mjs';
import {axisMarks} from './chart_scale.mjs';
import {Level} from './status_cell.mjs';

const AXIS_HEIGHT = 22;
const TICK_PX = 4;
const LABEL_BASELINE = 16;

/** Tick marks and labels for [from, to] across the plot starting at `left`; `grid` also draws a vertical rule down `gridHeight`. */
export function appendTimeTicks(svg, {from, to, left = 0, width, total, y, grid = 0}) {
  axisMarks(from, to, {left, width, total}).forEach(mark => {
    if (grid) svg.append(svgNode('line', {x1: mark.x, x2: mark.x, y1: y - grid, y2: y}, 'grid-line'));
    svg.append(svgNode('line', {x1: mark.x, x2: mark.x, y1: y, y2: y + TICK_PX}, 'axis-tick'));
    const text = svgNode('text', {x: mark.x, y: y + LABEL_BASELINE, 'text-anchor': mark.anchor}, mark.major ? 'axis-label major' : 'axis-label');
    text.textContent = mark.label;
    svg.append(text);
  });
}

/** One x-axis row for the strips above it; `inset` leaves room for a lane-label column on the left. */
export function timeAxis({from, to, inset = 0}) {
  const wrap = html('div', 'chart chart-axis');
  const holder = html('div', 'chart-plot');
  wrap.append(holder);
  responsive(holder, width => {
    holder.replaceChildren();
    const svg = svgNode('svg', {viewBox: `0 0 ${width} ${AXIS_HEIGHT}`, width, height: AXIS_HEIGHT, role: 'img',
      'aria-label': 'Time axis'});
    svg.append(svgNode('line', {x1: inset, x2: width, y1: 0.5, y2: 0.5}, 'axis-line'));
    appendTimeTicks(svg, {from, to, left: inset, width: width - inset, total: width, y: 0});
    holder.append(svg);
  });
  return wrap;
}

export const levelWord = level => ({nodata: 'no data', ok: 'ok', idle: 'idle', unknown: 'unknown', warn: 'needs attention', error: 'failing'}[level] ?? level);

const LEGEND_LEVELS = [Level.OK, Level.IDLE, Level.WARN, Level.ERROR, 'nodata'];

/** The state colours a strip uses, drawn once for a group of strips. */
export function stateLegend(levels = LEGEND_LEVELS) {
  const legend = html('div', 'chart-legend');
  levels.forEach(level => {
    const item = html('span', 'legend-item');
    item.append(html('span', `dot level-${level}`), levelWord(level));
    legend.append(item);
  });
  return legend;
}
