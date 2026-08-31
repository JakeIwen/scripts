export const TILE_ORDER_STORAGE_KEY = 'van-dashboard.tile-order.v1';

export const DEFAULT_TILE_ORDER = [
  'cop',
  'vonstar',
  'books',
  'video-library',
  'telemetry',
  'sonos-card',
  'storage',
  'system-monitor',
  'compute-worker',
  'usb-devices',
  'backups',
  'ignition-monitor',
  'price-checks',
  'lighting-card',
  'ubnt-wifi',
  'openwrt-card',
] as const;

export type TileId = (typeof DEFAULT_TILE_ORDER)[number];

export function normalizeTileOrder(stored: unknown, available: readonly TileId[]): TileId[] {
  const availableSet = new Set<TileId>(available);
  const result: TileId[] = [];

  if (Array.isArray(stored)) {
    for (const value of stored) {
      if (
        typeof value === 'string' &&
        availableSet.has(value as TileId) &&
        !result.includes(value as TileId)
      ) {
        result.push(value as TileId);
      }
    }
  }

  for (const tileId of available) {
    if (!result.includes(tileId)) result.push(tileId);
  }
  return result;
}

export function loadTileOrder(available: readonly TileId[]): TileId[] {
  try {
    const stored = JSON.parse(localStorage.getItem(TILE_ORDER_STORAGE_KEY) ?? 'null');
    return normalizeTileOrder(stored, available);
  } catch {
    return [...available];
  }
}

export function saveTileOrder(order: readonly TileId[]): void {
  localStorage.setItem(TILE_ORDER_STORAGE_KEY, JSON.stringify(order));
}
