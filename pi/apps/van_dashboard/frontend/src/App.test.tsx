import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { App } from './App';
import { ToastProvider } from './components/ToastProvider';

describe('Van Dashboard preview composition', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('keeps every legacy tile represented while data is loading', () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise<Response>(() => undefined)),
    );

    const view = render(
      <ToastProvider>
        <App />
      </ToastProvider>,
    );

    for (const title of [
      'COP ALERT',
      'vOnStar',
      'Audiobooks',
      'Movies & TV',
      'Telemetry',
      'Sonos',
      'Disk & Torrent',
      'System Health',
      'M4 Compute',
      'USB Devices',
      'Backups',
      'Ignition Monitor',
      'Deal Watch',
      'Lighting',
      'UBNT Wi-Fi',
      'OpenWrt',
    ]) {
      expect(screen.getByRole('heading', { name: title })).toBeInTheDocument();
    }

    view.unmount();
  });
});
