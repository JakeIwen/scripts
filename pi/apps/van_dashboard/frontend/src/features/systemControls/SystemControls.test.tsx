import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ToastProvider } from '../../components/ToastProvider';
import { useSystemControls } from './controls';
import { SystemControls } from './SystemControls';

vi.mock('./controls', () => ({ useSystemControls: vi.fn() }));

describe('SystemControls', () => {
  it('renders the two labeled rows and keeps tile editing available while actions are locked', () => {
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

    expect(screen.getByText('Pi')).toBeInTheDocument();
    expect(screen.getByText('Dash')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Restart dashboard' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Restart Pi' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Power down Pi' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Arrange tiles' })).toBeEnabled();
    const announcement = screen.getByText('Dashboard disconnected while vanpi reboots');
    expect(announcement).toHaveClass('visually-hidden');
  });
});
