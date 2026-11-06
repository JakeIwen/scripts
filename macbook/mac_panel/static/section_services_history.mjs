// Service health timelines: one history request per range feeds every service's strip.
import {RANGES, rangeToggle, rangeWindow, stateStrip} from './charts.mjs';
import {api} from './panel_api.mjs';
import {emptyState, node} from './panel_dom.mjs';

const CACHE_MS = 60000;
// The sampler writes once a minute; a longer silence means the panel was down, not that the state held.
const MAX_GAP_SECONDS = 300;
const cache = new Map();
let range = '24h';

function load(key) {
  const hit = cache.get(key);
  if (hit && Date.now() - hit.at < CACHE_MS) return hit.promise;
  const {from, to} = rangeWindow(key, Date.now() / 1000);
  const promise = api(`/api/history?section=services&since=${from}&until=${to}`).then(body => ({from, to, series: body.series}));
  cache.set(key, {at: Date.now(), promise});
  promise.catch(() => { if (cache.get(key)?.promise === promise) cache.delete(key); });
  return promise;
}

/** Draws every `.history-slot` under `root` for the current range. */
export function fillSlots(root) {
  const slots = [...root.querySelectorAll('.history-slot')];
  const asked = range;
  slots.forEach(slot => { slot.dataset.range = asked; });
  load(asked).then(({from, to, series}) => {
    slots.forEach(slot => {
      if (slot.dataset.range !== asked) return;
      const samples = series[slot.dataset.label] ?? [];
      slot.replaceChildren(samples.length ? stateStrip({samples, from, to, maxGap: MAX_GAP_SECONDS})
        : emptyState('No history recorded for this range yet'));
    });
  }).catch(() => {
    slots.forEach(slot => slot.replaceChildren(node('p', 'History is unavailable', 'muted small')));
  });
}

export const historySlot = label => {
  const slot = node('div', null, 'history-slot');
  slot.dataset.label = label;
  return slot;
};

/** The shared 24 h / 7 d choice; changing it redraws every strip inside `container()`. */
export const historyToggle = container => rangeToggle({
  ranges: Object.keys(RANGES), value: range,
  onChange(next) { range = next; fillSlots(container()); },
});
