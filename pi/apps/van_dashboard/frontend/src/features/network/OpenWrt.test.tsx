import { fireEvent, render, screen } from '@testing-library/react';
import { beforeAll, describe, expect, it, vi } from 'vitest';

import { OpenWrtSheet } from './OpenWrtSheet';
import { OpenWrtTile } from './OpenWrtTile';
import { sampleClients, sampleConnectivity, sampleSpeedtest } from './testFixtures';

beforeAll(() => {
  if (!HTMLDialogElement.prototype.showModal) {
    HTMLDialogElement.prototype.showModal = function showModal() {
      this.setAttribute('open', '');
    };
  }
  if (!HTMLDialogElement.prototype.close) {
    HTMLDialogElement.prototype.close = function close() {
      this.removeAttribute('open');
      this.dispatchEvent(new Event('close'));
    };
  }
});

describe('OpenWrt UI', () => {
  it('renders the active route and read-only speed-test result on the tile', () => {
    const onOpen = vi.fn();
    render(
      <OpenWrtTile
        connectivity={sampleConnectivity()}
        connectivityError={null}
        connectivityRefreshing={false}
        speedtest={sampleSpeedtest()}
        speedtestError={null}
        onOpen={onOpen}
      />,
    );

    expect(screen.getAllByText('clientwan').length).toBeGreaterThan(0);
    expect(screen.getByText('↓ 42.5 Mbps · ↑ 8.3 Mbps')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /speed test/i })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Open OpenWrt details' }));
    expect(onOpen).toHaveBeenCalledOnce();
  });

  it('renders connected clients and starts a speed test from the details sheet', () => {
    const onClose = vi.fn();
    const onRefreshClients = vi.fn().mockResolvedValue(sampleClients());
    const onStartSpeedtest = vi.fn().mockResolvedValue(undefined);
    render(
      <OpenWrtSheet
        open
        onClose={onClose}
        connectivity={sampleConnectivity()}
        connectivityError={null}
        clients={sampleClients()}
        clientsError={null}
        clientsInitialLoading={false}
        clientsRefreshing={false}
        onRefreshClients={onRefreshClients}
        speedtest={sampleSpeedtest()}
        speedtestError={null}
        speedtestStarting={false}
        onStartSpeedtest={onStartSpeedtest}
      />,
    );

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('lion_fone')).toBeInTheDocument();
    expect(screen.getByText('2.4 GHz · radio0')).toBeInTheDocument();
    expect(screen.getByText('↓ 1.0 MiB · ↑ 512 KiB')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    expect(onRefreshClients).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button', { name: 'Run speed test' }));
    expect(onStartSpeedtest).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button', { name: 'Close OpenWrt' }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
