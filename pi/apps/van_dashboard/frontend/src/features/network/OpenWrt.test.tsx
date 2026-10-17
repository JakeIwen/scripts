import { fireEvent, render, screen } from '@testing-library/react';
import { beforeAll, describe, expect, it, vi } from 'vitest';

import { OpenWrtSheet } from './OpenWrtSheet';
import { OpenWrtTile } from './OpenWrtTile';
import { sampleClients, sampleConnectivity, sampleSpeedtest } from './testFixtures';
import { presentWanThroughput } from './presentation';
import type { WanThroughput } from './throughput';

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
  it('shows degraded HTTPS health and keeps the selected route visible', () => {
    const connectivity = sampleConnectivity();
    connectivity.router.interfaces[1] = {
      name: 'wan',
      state: 'degraded',
      tracking: 'active',
      detail: 'mwan3 ping: online; HTTPS degraded; Cloudflare HTTP 000, curl 28',
    };
    const view = render(
      <OpenWrtTile
        connectivity={connectivity}
        connectivityError={null}
        connectivityRefreshing={false}
        throughput={null}
        speedtest={null}
        speedtestError={null}
        speedtestStarting={false}
        onStartSpeedtest={vi.fn().mockResolvedValue(undefined)}
        onOpen={vi.fn()}
      />,
    );
    expect(screen.getByText('wan · degraded')).toHaveClass('degraded');
    expect(screen.getByText('wan · degraded')).toHaveAttribute(
      'title',
      expect.stringContaining('curl 28'),
    );
    expect(screen.getByText('clientwan')).toBeInTheDocument();
    view.unmount();
  });

  it('renders the active route and read-only speed-test result on the tile', () => {
    const onOpen = vi.fn();
    const onStartSpeedtest = vi.fn(async () => undefined);
    render(
      <OpenWrtTile
        connectivity={sampleConnectivity()}
        connectivityError={null}
        connectivityRefreshing={false}
        throughput={null}
        speedtest={sampleSpeedtest()}
        speedtestError={null}
        speedtestStarting={false}
        onStartSpeedtest={onStartSpeedtest}
        onOpen={onOpen}
      />,
    );

    expect(screen.getAllByText('clientwan').length).toBeGreaterThan(0);
    expect(screen.getByText('↓ 42.5 Mbps · ↑ 8.3 Mbps')).toBeInTheDocument();
    expect(screen.getByText(/Latency 31\.2 ms @/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /speed test/i }));
    expect(onStartSpeedtest).toHaveBeenCalledOnce();

    fireEvent.click(screen.getByRole('button', { name: 'Open OpenWrt details' }));
    expect(onOpen).toHaveBeenCalledOnce();
  });

  it('uses the compact tile hierarchy instead of rendering an online pill', () => {
    const view = render(
      <OpenWrtTile
        connectivity={sampleConnectivity()}
        connectivityError={null}
        connectivityRefreshing={false}
        throughput={null}
        speedtest={sampleSpeedtest()}
        speedtestError={null}
        speedtestStarting={false}
        onStartSpeedtest={vi.fn().mockResolvedValue(undefined)}
        onOpen={vi.fn()}
      />,
    );
    const tile = view.container.querySelector('.openwrt-tile');
    const header = tile?.querySelector(':scope > .connectivity-head');
    const overview = tile?.querySelector(':scope > .mwan-overview');
    const footer = tile?.querySelector(':scope > .openwrt-speedtest');

    expect(header?.children[0]).toHaveClass('network-card-heading');
    expect(header?.children[1]).toHaveClass('connectivity-age');
    expect(header?.children[1]).toHaveTextContent(/^Updated · /);
    expect(tile?.querySelector('.status-pill')).not.toBeInTheDocument();

    expect(overview?.children[0]).toHaveClass('mwan-primary');
    expect(overview?.children[1]).toHaveClass('mwan-list');
    expect(overview?.querySelector('.network-label')).toHaveTextContent('MWAN3 route');
    expect(overview?.querySelector('.network-dot')).toHaveClass('good');
    expect(overview?.querySelectorAll('.mwan-chip')).toHaveLength(2);

    expect(footer?.children[0]).toHaveClass('openwrt-speedtest-button');
    expect(footer?.children[1]).toHaveClass('speed-results');
    expect(footer?.children[1]).toHaveAttribute('role', 'status');
  });

  it('shows current internet throughput under the route once two samples exist', () => {
    const connectivity = sampleConnectivity();
    const tile = (throughput: WanThroughput | null, status = connectivity) => (
      <OpenWrtTile
        connectivity={status}
        connectivityError={null}
        connectivityRefreshing={false}
        throughput={presentWanThroughput(status, throughput)}
        speedtest={null}
        speedtestError={null}
        speedtestStarting={false}
        onStartSpeedtest={vi.fn().mockResolvedValue(undefined)}
        onOpen={vi.fn()}
      />
    );
    const view = render(tile(null));
    // Collectors without counters leave the tile unchanged.
    expect(screen.queryByLabelText('Current internet throughput')).not.toBeInTheDocument();

    connectivity.router.wanTraffic = { uptimeSeconds: 100, uplinks: [] };
    view.rerender(tile(null));
    expect(screen.getByLabelText('Current internet throughput')).toHaveTextContent('NetMeasuring…');

    const throughput = {
      downloadBps: 4_200_000,
      uploadBps: 640_000,
      windowSeconds: 3,
      uplinks: [{ name: 'wan', downloadBps: 4_200_000, uploadBps: 640_000 }],
    };
    view.rerender(tile(throughput));
    const row = screen.getByLabelText('Current internet throughput');
    expect(row.closest('.mwan-primary')).not.toBeNull();
    expect(row).toHaveTextContent('↓ 4.2 Mbps · ↑ 640 kbps');
    expect(row).toHaveAttribute('title', 'wan ↓ 4.2 Mbps · ↑ 640 kbps\nAveraged over 3.0 s');

    view.rerender(tile(throughput, { ...connectivity, stale: true }));
    expect(screen.getByLabelText('Current internet throughput')).toHaveTextContent('Net—');
    view.unmount();
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
