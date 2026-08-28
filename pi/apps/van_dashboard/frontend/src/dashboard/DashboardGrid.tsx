import { useMemo, useState } from 'react';

import type { TileId } from './tileOrder';
import { useTileOrder } from './useTileOrder';

export interface DashboardTile {
  id: TileId;
  content: React.ReactNode;
}

interface DashboardGridProps {
  tiles: readonly DashboardTile[];
  editing: boolean;
}

export function DashboardGrid({ tiles, editing }: DashboardGridProps) {
  const available = useMemo(() => tiles.map((tile) => tile.id), [tiles]);
  const { order, moveBy, moveBefore } = useTileOrder(available);
  const [dragging, setDragging] = useState<TileId | null>(null);
  const [announcement, setAnnouncement] = useState('');
  const tileById = useMemo(() => new Map(tiles.map((tile) => [tile.id, tile])), [tiles]);

  return (
    <main className={`dashboard-grid ${editing ? 'dashboard-grid--editing' : ''}`}>
      <p className="visually-hidden" aria-live="polite">
        {announcement}
      </p>
      {order.map((tileId, index) => {
        const tile = tileById.get(tileId);
        if (!tile) return null;
        return (
          <section
            className="dashboard-grid__item"
            data-tile-id={tileId}
            draggable={editing}
            key={tileId}
            onDragStart={() => setDragging(tileId)}
            onDragEnd={() => setDragging(null)}
            onDragOver={(event) => {
              if (editing) event.preventDefault();
            }}
            onDrop={(event) => {
              event.preventDefault();
              if (dragging) moveBefore(dragging, tileId);
              if (dragging) setAnnouncement(`${dragging} moved before ${tileId}`);
              setDragging(null);
            }}
            onPointerDown={(event) => {
              if (!editing || event.pointerType === 'mouse' || event.button !== 0) return;
              if ((event.target as Element).closest('button')) return;
              event.currentTarget.setPointerCapture(event.pointerId);
              setDragging(tileId);
            }}
            onPointerMove={(event) => {
              if (!editing || !dragging || event.pointerType === 'mouse') return;
              const target = document
                .elementFromPoint(event.clientX, event.clientY)
                ?.closest<HTMLElement>('[data-tile-id]')?.dataset.tileId as TileId | undefined;
              if (target && target !== dragging) {
                moveBefore(dragging, target);
                setAnnouncement(`${dragging} moved before ${target}`);
              }
            }}
            onPointerUp={(event) => {
              if (event.currentTarget.hasPointerCapture(event.pointerId)) {
                event.currentTarget.releasePointerCapture(event.pointerId);
              }
              setDragging(null);
            }}
            onPointerCancel={() => setDragging(null)}
          >
            {editing && (
              <div className="tile-order-controls" aria-label={`Move ${tileId} tile`}>
                <button
                  type="button"
                  onClick={() => {
                    moveBy(tileId, -1);
                    setAnnouncement(`${tileId} moved earlier`);
                  }}
                  disabled={index === 0}
                >
                  ←<span className="visually-hidden">Move earlier</span>
                </button>
                <span aria-hidden="true">Drag</span>
                <button
                  type="button"
                  onClick={() => {
                    moveBy(tileId, 1);
                    setAnnouncement(`${tileId} moved later`);
                  }}
                  disabled={index === order.length - 1}
                >
                  →<span className="visually-hidden">Move later</span>
                </button>
              </div>
            )}
            <div className={editing ? 'tile-interactions-disabled' : ''}>{tile.content}</div>
          </section>
        );
      })}
    </main>
  );
}
