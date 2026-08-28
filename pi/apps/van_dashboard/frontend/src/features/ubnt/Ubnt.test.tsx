import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import type { PollingState } from '../../hooks/usePollingResource';
import type { UbntControls } from './controls';
import { UbntSheet } from './UbntSheet';
import { UbntTile } from './UbntTile';
import { sampleUbntStatus } from './testFixtures';
import type { UbntWifiStatus } from './types';

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

afterEach(cleanup);

function resource(status: UbntWifiStatus): PollingState<UbntWifiStatus> {
  return {
    data: status,
    error: null,
    initialLoading: false,
    refreshing: false,
    lastUpdatedAt: Date.now(),
    refresh: vi.fn(async () => status),
  };
}

function controls(overrides: Partial<UbntControls> = {}): UbntControls {
  return {
    busy: false,
    scan: vi.fn().mockResolvedValue(true),
    connect: vi.fn().mockResolvedValue(true),
    provision: vi.fn().mockResolvedValue(true),
    resumeAutomatic: vi.fn().mockResolvedValue(true),
    updateProfile: vi.fn().mockResolvedValue(true),
    ...overrides,
  };
}

describe('UBNT UI', () => {
  it('shows physical and radio state on the tile', () => {
    const onOpen = vi.fn();
    render(
      <UbntTile status={sampleUbntStatus()} error={null} refreshing={false} onOpen={onOpen} />,
    );

    expect(screen.getByText('denlink · -56 dBm · 99.1% CCQ')).toBeInTheDocument();
    expect(screen.getByText('Open details to manage antenna Wi-Fi')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Open UBNT Wi-Fi details' }));
    expect(onOpen).toHaveBeenCalledOnce();
  });

  it('shows profile, scan, connect, and provisioning controls without Starlink coupling', () => {
    const actions = controls();
    render(
      <UbntSheet
        open
        onClose={vi.fn()}
        resource={resource(sampleUbntStatus())}
        controls={actions}
      />,
    );

    expect(screen.getAllByText('denlink').length).toBeGreaterThan(0);
    expect(screen.getByText('Campus')).toBeInTheDocument();
    expect(screen.queryByText(/starlink/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Scan nearby Wi-Fi' }));
    expect(actions.scan).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button', { name: 'Reconnect' }));
    expect(actions.connect).toHaveBeenCalledWith('denlink');
    expect(screen.getByRole('button', { name: 'Refresh status' })).toBeInTheDocument();
  });

  it('clears a WPA password field as soon as provisioning starts', async () => {
    const actions = controls();
    render(
      <UbntSheet
        open
        onClose={vi.fn()}
        resource={resource(sampleUbntStatus())}
        controls={actions}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    const password = screen.getByLabelText('Wi-Fi password');
    fireEvent.change(password, { target: { value: 'not-a-real-secret' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save & connect' }));

    await waitFor(() => expect(actions.provision).toHaveBeenCalledOnce());
    expect(screen.queryByLabelText('Wi-Fi password')).not.toBeInTheDocument();
    expect(screen.queryByText('not-a-real-secret')).not.toBeInTheDocument();
    expect(actions.provision).toHaveBeenCalledWith({
      ssid: 'Camp Secure',
      security: 'wpa',
      bssid: '00:11:22:33:44:88',
      password: 'not-a-real-secret',
    });
  });

  it('edits every non-secret profile field and never preloads a password', async () => {
    const actions = controls();
    render(
      <UbntSheet
        open
        onClose={vi.fn()}
        resource={resource(sampleUbntStatus())}
        controls={actions}
      />,
    );

    fireEvent.click(screen.getAllByRole('button', { name: 'Edit' })[0]!);
    const password = screen.getByLabelText(/New Wi-Fi password/);
    expect(password).toHaveValue('');
    fireEvent.change(password, { target: { value: 'replacement-secret' } });
    fireEvent.change(screen.getByLabelText('Lock to AP'), {
      target: { value: 'AA:BB:CC:DD:EE:FF' },
    });
    fireEvent.change(screen.getByRole('slider', { name: 'Output power in dBm' }), {
      target: { value: '19' },
    });
    fireEvent.change(screen.getByLabelText('Data Rate Module'), {
      target: { value: 'ewma_ht' },
    });
    fireEvent.click(screen.getByLabelText('Maximum TX rate: Auto'));
    fireEvent.change(screen.getByLabelText('Manual maximum TX rate'), {
      target: { value: '7' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save & reconnect now' }));

    await waitFor(() => expect(actions.updateProfile).toHaveBeenCalledOnce());
    expect(screen.queryByLabelText(/New Wi-Fi password/)).not.toBeInTheDocument();
    expect(actions.updateProfile).toHaveBeenCalledWith({
      profile: 'denlink',
      password: 'replacement-secret',
      bssid: 'AA:BB:CC:DD:EE:FF',
      outputPowerDbm: 19,
      rateModule: 'ewma_ht',
      rateAuto: false,
      rateMcs: 7,
      applyNow: true,
    });
  });

  it('keeps every mutation disabled while the backend reports a running operation', () => {
    render(
      <UbntSheet
        open
        onClose={vi.fn()}
        resource={resource(sampleUbntStatus('running'))}
        controls={controls()}
      />,
    );

    expect(screen.getByRole('button', { name: 'Scan nearby Wi-Fi' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Reconnect' })).toBeDisabled();
    expect(screen.getAllByRole('button', { name: 'Edit' })[0]).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Add' })).toBeDisabled();
  });

  it('provisions open networks without a password and resumes automatic selection', () => {
    const status = sampleUbntStatus();
    status.state.automaticPaused = true;
    const actions = controls();
    render(<UbntSheet open onClose={vi.fn()} resource={resource(status)} controls={actions} />);

    fireEvent.click(screen.getByRole('button', { name: 'Add & connect' }));
    expect(actions.provision).toHaveBeenCalledWith({
      ssid: 'Park Guest',
      security: 'none',
      bssid: '00:11:22:33:44:99',
      password: '',
    });
    fireEvent.click(screen.getByRole('button', { name: 'Resume automatic selection' }));
    expect(actions.resumeAutomatic).toHaveBeenCalledOnce();
  });
});
