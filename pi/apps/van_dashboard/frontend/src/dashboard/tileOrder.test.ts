import { beforeEach, describe, expect, it } from 'vitest';

import { DEFAULT_TILE_ORDER, loadTileOrder, normalizeTileOrder, saveTileOrder } from './tileOrder';

describe('tile ordering', () => {
  beforeEach(() => localStorage.clear());

  it('keeps known unique IDs and appends newly available tiles', () => {
    const available = DEFAULT_TILE_ORDER.slice(0, 5);
    expect(normalizeTileOrder(['telemetry', 'cop', 'cop', 'removed'], available)).toEqual([
      'telemetry',
      'cop',
      'vonstar',
      'books',
      'video-library',
    ]);
  });

  it('round-trips the legacy localStorage key', () => {
    const order = DEFAULT_TILE_ORDER.slice(0, 4);
    saveTileOrder(order);
    expect(loadTileOrder(order)).toEqual(order);
    expect(localStorage.getItem('van-dashboard.tile-order.v1')).toBe(JSON.stringify(order));
  });

  it('falls back safely when stored JSON is malformed', () => {
    localStorage.setItem('van-dashboard.tile-order.v1', '{');
    const available = DEFAULT_TILE_ORDER.slice(0, 3);
    expect(loadTileOrder(available)).toEqual(available);
  });
});
