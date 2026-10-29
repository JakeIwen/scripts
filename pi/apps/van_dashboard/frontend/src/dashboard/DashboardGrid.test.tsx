import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { DashboardGrid } from './DashboardGrid';

describe('DashboardGrid editing', () => {
  beforeEach(() => localStorage.clear());

  it('offers readable move controls and announces keyboard-friendly reordering', () => {
    render(
      <DashboardGrid
        editing
        tiles={[
          { id: 'cop', content: <div>COP tile</div> },
          { id: 'books', content: <div>Books tile</div> },
        ]}
      />,
    );

    fireEvent.click(screen.getAllByText('Move later')[0]!.closest('button')!);
    expect(screen.getByText('cop moved later')).toBeInTheDocument();
    expect(localStorage.getItem('van-dashboard.tile-order.v1')).toBe('["books","cop"]');
  });

  it('reorders by mouse drag, filling dataTransfer so Firefox starts the drag', () => {
    const { container } = render(
      <DashboardGrid
        editing
        tiles={[
          { id: 'cop', content: <div>COP tile</div> },
          { id: 'books', content: <div>Books tile</div> },
        ]}
      />,
    );
    const tile = (id: string) => container.querySelector(`[data-tile-id="${id}"]`)!;
    const dataTransfer = { setData: vi.fn(), effectAllowed: 'all', dropEffect: 'none' };

    fireEvent.dragStart(tile('books'), { dataTransfer });
    fireEvent.dragOver(tile('cop'), { dataTransfer });
    fireEvent.drop(tile('cop'), { dataTransfer });

    expect(dataTransfer.setData).toHaveBeenCalledWith('text/plain', 'books');
    expect(localStorage.getItem('van-dashboard.tile-order.v1')).toBe('["books","cop"]');
  });
});
