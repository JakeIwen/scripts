import { useCallback, useState } from 'react';

import { loadTileOrder, saveTileOrder, type TileId } from './tileOrder';

export function useTileOrder(available: readonly TileId[]) {
  const [order, setOrder] = useState<TileId[]>(() => loadTileOrder(available));

  const persist = useCallback((next: TileId[]) => {
    setOrder(next);
    saveTileOrder(next);
  }, []);

  const moveBy = useCallback((tileId: TileId, distance: number) => {
    setOrder((current) => {
      const from = current.indexOf(tileId);
      if (from < 0) return current;
      const to = Math.max(0, Math.min(current.length - 1, from + distance));
      if (from === to) return current;
      const next = [...current];
      next.splice(from, 1);
      next.splice(to, 0, tileId);
      saveTileOrder(next);
      return next;
    });
  }, []);

  const moveBefore = useCallback((tileId: TileId, targetId: TileId) => {
    setOrder((current) => {
      if (tileId === targetId) return current;
      const next = current.filter((value) => value !== tileId);
      const targetIndex = next.indexOf(targetId);
      if (targetIndex < 0) return current;
      next.splice(targetIndex, 0, tileId);
      saveTileOrder(next);
      return next;
    });
  }, []);

  return { order, persist, moveBy, moveBefore };
}
