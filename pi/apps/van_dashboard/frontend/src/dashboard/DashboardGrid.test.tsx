import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';

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
});
