import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ToastProvider } from '../../components/ToastProvider';
import { useSystemControls } from './controls';
import { SystemControls } from './SystemControls';

vi.mock('./controls', () => ({ useSystemControls: vi.fn() }));

describe('SystemControls', () => {
  it('renders readable terminal controls and locks every button together', () => {
    vi.mocked(useSystemControls).mockReturnValue({
      running: false,
      locked: true,
      phase: 'power-disconnected',
      message: 'Dashboard disconnected while vanpi reboots',
      error: null,
      operation: null,
      requestPower: vi.fn(),
      restartDashboard: vi.fn(),
    });
    render(
      <ToastProvider>
        <SystemControls />
      </ToastProvider>,
    );

    expect(screen.getByRole('button', { name: 'Restart UI' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Reboot' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Power down' })).toBeDisabled();
    expect(screen.getByText('Dashboard disconnected while vanpi reboots')).toBeInTheDocument();
  });
});
