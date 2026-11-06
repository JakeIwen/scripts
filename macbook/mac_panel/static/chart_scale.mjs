// Pure scale maths shared by every chart and its axes (tested under Node): time and value to pixels, "nice"
// tick values, local-time tick positions and label placement. Times are epoch seconds.

export const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
export const round = value => Math.round(value * 10) / 10;

export const timeToX = (ts, {from, to, width}) => clamp((ts - from) / (to - from), 0, 1) * width;
export const xToTime = (x, {from, to, width}) => from + clamp(x / width, 0, 1) * (to - from);

const NICE_FACTORS = [1, 2, 2.5, 5, 10, 20, 25, 50, 100];
const DAY_SECONDS = 86400;
const MIN_HOUR_LABEL_PX = 52;
const MIN_DAY_LABEL_PX = 44;
const CHAR_PX = 6.6;

/**
 * Round tick values covering [min, max] with at most `count` ticks. The first and last tick bound the data,
 * so a caller that autoscales uses them as the axis ends: 0..100 gives 0/25/50/75/100.
 */
export function niceTicks(min, max, count = 5) {
  let low = Number.isFinite(min) ? min : 0;
  let high = Number.isFinite(max) ? max : low + 1;
  if (high < low) [low, high] = [high, low];
  if (high === low) {
    const pad = Math.abs(low) * 0.1 || 1;
    [low, high] = [low - pad, high + pad];
  }
  const intervals = Math.max(1, Math.floor(count) - 1);
  const rough = (high - low) / intervals;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  let step = magnitude * 100;
  for (const factor of NICE_FACTORS) {
    const candidate = magnitude * factor;
    const first = Math.floor(low / candidate + 1e-9);
    const last = Math.ceil(high / candidate - 1e-9);
    if (last - first <= intervals) { step = candidate; break; }
  }
  const start = Math.floor(low / step + 1e-9);
  const end = Math.ceil(high / step - 1e-9);
  const places = Math.max(0, -Math.floor(Math.log10(step)) + 1);
  const ticks = [];
  for (let index = start; index <= end; index += 1) ticks.push(Number((index * step).toFixed(places)));
  return {ticks, step, min: ticks[0], max: ticks[ticks.length - 1]};
}

// ---- time ticks ----

const HOUR_STEPS = [1, 2, 3, 4, 6, 12];
const DAY_STEPS = [1, 2, 3, 7];

/** Short clock label in the viewer's locale, e.g. "2 PM" or "14:00". */
export const hourLabel = (ts, locale) => new Date(ts * 1000).toLocaleTimeString(locale, {hour: 'numeric'});
export const dayLabel = (ts, locale) => new Date(ts * 1000).toLocaleDateString(locale, {weekday: 'short'});
export const dateLabel = (ts, locale) => new Date(ts * 1000).toLocaleDateString(locale, {month: 'short', day: 'numeric'});

/** A moment as a short label: the clock for today, the weekday and clock for earlier days. */
export function momentLabel(ts, now, locale) {
  const clock = new Date(ts * 1000).toLocaleTimeString(locale, {hour: 'numeric', minute: '2-digit'});
  const sameDay = new Date(ts * 1000).toDateString() === new Date(now * 1000).toDateString();
  return sameDay ? clock : `${dayLabel(ts, locale)} ${clock}`;
}

function localSteps(from, to, step, unit) {
  const start = new Date(from * 1000);
  const cursor = unit === 'hour'
    ? new Date(start.getFullYear(), start.getMonth(), start.getDate(), Math.floor(start.getHours() / step) * step)
    : new Date(start.getFullYear(), start.getMonth(), start.getDate());
  const stamps = [];
  for (let guard = 0; guard < 400; guard += 1) {
    const ts = cursor.getTime() / 1000;
    if (ts > to) break;
    if (ts >= from) stamps.push(ts);
    if (unit === 'hour') cursor.setHours(cursor.getHours() + step);
    else cursor.setDate(cursor.getDate() + step);
  }
  return stamps;
}

/**
 * Tick positions for [from, to] drawn `width` px wide: local hours (aligned to the step) for spans up to two
 * days, local midnights otherwise, spaced at least a label apart. Midnight ticks are major and carry the
 * day name, so a 24 h axis shows where yesterday ends. Returns [{ts, label, major}].
 */
export function timeTicks(from, to, width, locale) {
  const span = to - from;
  const byHour = span <= 2 * DAY_SECONDS;
  const steps = byHour ? HOUR_STEPS : DAY_STEPS;
  const unitSeconds = byHour ? 3600 : DAY_SECONDS;
  const minPx = byHour ? MIN_HOUR_LABEL_PX : MIN_DAY_LABEL_PX;
  const step = steps.find(candidate => (candidate * unitSeconds / span) * width >= minPx) ?? steps[steps.length - 1];
  return localSteps(from, to, step, byHour ? 'hour' : 'day').map(ts => {
    const date = new Date(ts * 1000);
    const midnight = date.getHours() === 0 && date.getMinutes() === 0;
    if (byHour) return {ts, major: midnight, label: midnight ? dayLabel(ts, locale) : hourLabel(ts, locale)};
    return {ts, major: true, label: step >= 7 || width / (span / DAY_SECONDS) < 60 ? dayLabel(ts, locale) : `${dayLabel(ts, locale)} ${date.getDate()}`};
  });
}

/** Text anchor that keeps a label of `chars` characters centred on x without leaving [0, width]. */
export function labelAnchor(x, width, chars) {
  const half = chars * CHAR_PX / 2;
  if (x - half < 0) return 'start';
  if (x + half > width) return 'end';
  return 'middle';
}

export const tickLabelWidth = chars => Math.ceil(chars * CHAR_PX);

/** Moves labels apart vertically so none is closer than `gap` px to the next, within [low, high]. */
export function spreadLabels(ys, gap, low, high) {
  const order = ys.map((y, index) => ({y, index})).sort((a, b) => a.y - b.y);
  order.forEach((entry, position) => {
    entry.y = Math.max(entry.y, position ? order[position - 1].y + gap : low);
  });
  for (let position = order.length - 1; position >= 0; position -= 1) {
    const ceiling = position === order.length - 1 ? high : order[position + 1].y - gap;
    order[position].y = Math.min(order[position].y, ceiling);
  }
  const result = new Array(ys.length);
  order.forEach(entry => { result[entry.index] = round(Math.max(entry.y, low)); });
  return result;
}

/** Time ticks placed on an axis: x within the plot (offset by `left`), label and anchor kept inside `total`. */
export function axisMarks(from, to, {left = 0, width, total = left + width, locale}) {
  return timeTicks(from, to, width, locale).map(tick => {
    const x = round(left + timeToX(tick.ts, {from, to, width}));
    return {...tick, x, anchor: labelAnchor(x, total, tick.label.length)};
  });
}
