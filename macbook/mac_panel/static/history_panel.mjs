// A range-toggled block of history charts: fetches /api/history once per section and range, caches it briefly
// so the 15 s polls redraw without refetching, and hands the series to the caller's draw function.
import {api} from './panel_api.mjs';
import {RANGES, rangeToggle, rangeWindow} from './charts.mjs';
import {emptyState, loadError, node} from './panel_dom.mjs';

const FRESH_MS = 60000;
const DEFAULT_RANGE = '24h';
const cache = new Map();
const chosen = new Map();

function load(section, range) {
  const key = `${section}:${range}`;
  const hit = cache.get(key);
  if (hit && Date.now() - hit.at < FRESH_MS) return hit;
  const window = rangeWindow(range, Date.now() / 1000);
  const entry = {at: Date.now(), window, series: null, promise: null};
  const query = `section=${encodeURIComponent(section)}&since=${window.from}&until=${window.to}`;
  entry.promise = api(`/api/history?${query}`).then(result => {
    entry.series = result.series;
    return entry;
  }, error => {
    cache.delete(key);
    throw error;
  });
  cache.set(key, entry);
  return entry;
}

/**
 * id remembers the chosen range between renders; draw(series, {from, to}) returns an array of nodes,
 * empty when the range holds nothing to draw.
 */
export function historyPanel({id, section, draw, emptyText}) {
  const root = node('div', null, 'history');
  const body = node('div');
  let range = chosen.get(id) ?? DEFAULT_RANGE;
  const paint = entry => {
    const content = draw(entry.series, entry.window);
    body.replaceChildren(...(content.length ? content : [emptyState(emptyText)]));
  };
  const show = () => {
    const entry = load(section, range);
    if (entry.series) {
      paint(entry);
      return;
    }
    body.replaceChildren(node('p', 'Loading history…', 'loading'));
    const asked = range;
    entry.promise.then(done => { if (asked === range) paint(done); },
      error => { if (asked === range) body.replaceChildren(loadError(`Could not load history: ${error.message}`, show)); });
  };
  root.append(rangeToggle({ranges: Object.keys(RANGES), value: range, onChange: next => {
    range = next;
    chosen.set(id, next);
    show();
  }}), body);
  show();
  return root;
}
