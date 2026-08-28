import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { fetchDiskStatus, fetchStoragePolicy } from './api';
import type { StorageControls } from './controls';
import { decodeDiskStatusResponse, decodeStoragePolicyResponse } from './decoders';
import {
  STORAGE_SHEET_POLL_INTERVAL_MS,
  STORAGE_TILE_POLL_INTERVAL_MS,
  useStorageResources,
} from './hooks';
import { assessUsbReset } from './resetEligibility';
import { StorageSheet } from './StorageSheet';
import { StorageTile } from './StorageTile';
import {
  diskStatusPayload,
  sampleDiskStatus,
  sampleStoragePolicy,
  storagePolicyPayload,
} from './testFixtures';
import { sampleUsbStatus } from '../usb/testFixtures';
import type { UsbControls } from '../usb/controls';

vi.mock('./api', () => ({
  fetchDiskStatus: vi.fn(),
  fetchStoragePolicy: vi.fn(),
}));
const fetchDiskStatusMock = vi.mocked(fetchDiskStatus);
const fetchStoragePolicyMock = vi.mocked(fetchStoragePolicy);

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

function StorageProbe({ sheetOpen }: { sheetOpen: boolean }) {
  const resources = useStorageResources(sheetOpen);
  return <output>{resources.disks.data?.disks.length ?? 'loading'}</output>;
}

function resource<T>(data: T) {
  return {
    data,
    error: null,
    initialLoading: false,
    refreshing: false,
    lastUpdatedAt: Date.now(),
    refresh: vi.fn().mockResolvedValue(data),
  };
}

function storageControls(): StorageControls {
  return {
    running: false,
    blocked: false,
    uncertainOutcome: false,
    pendingDiskOperation: null,
    lastMessage: null,
    lastError: null,
    setPolicy: vi.fn().mockResolvedValue(undefined),
    runDiskAction: vi.fn().mockResolvedValue(undefined),
    reconcileDiskStatus: vi.fn(),
  };
}

function usbControls(): UsbControls {
  return {
    running: false,
    blocked: false,
    uncertainOutcome: false,
    pendingOperation: null,
    lastMessage: null,
    lastError: null,
    discover: vi.fn().mockResolvedValue(undefined),
    runPortAction: vi.fn().mockResolvedValue(undefined),
    recoverUsb2: vi.fn().mockResolvedValue(undefined),
    reconcile: vi.fn(),
  };
}

describe('storage feature', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    fetchDiskStatusMock.mockResolvedValue(sampleDiskStatus());
    fetchStoragePolicyMock.mockResolvedValue(sampleStoragePolicy());
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('strictly decodes policy runtime and disk health evidence', () => {
    const policy = decodeStoragePolicyResponse(storagePolicyPayload());
    const disks = decodeDiskStatusResponse(diskStatusPayload());
    expect(policy.runtime).toMatchObject({
      disksMounted: true,
      qbittorrentRunning: true,
    });
    expect(disks.disks[0]?.health).toMatchObject({
      state: 'healthy',
      basis: 'offline_check',
      repairable: true,
    });
  });

  it('rejects inconsistent policy mount state', () => {
    const payload = storagePolicyPayload();
    const policy = payload.policy as Record<string, unknown>;
    const runtime = policy.runtime as Record<string, unknown>;
    runtime.disks_mounted = false;
    expect(() => decodeStoragePolicyResponse(payload)).toThrow(
      'policy runtime mount state is inconsistent',
    );
  });

  it('explains reset safety using the shared USB snapshot', () => {
    const disks = sampleDiskStatus();
    const mounted = disks.disks[0];
    if (!mounted) throw new Error('missing disk fixture');
    expect(assessUsbReset(mounted, disks.operation, sampleUsbStatus())).toMatchObject({
      eligible: false,
      label: 'Unmount first',
    });

    const unmounted = { ...mounted, mounted: false, mountpoints: [] };
    expect(assessUsbReset(unmounted, disks.operation, sampleUsbStatus())).toMatchObject({
      eligible: true,
      label: 'Eligible',
      port: { key: '2-2:1' },
    });

    const staleUsb = sampleUsbStatus();
    staleUsb.inventory.lastError = 'usb_watch timed out';
    expect(assessUsbReset(unmounted, disks.operation, staleUsb)).toMatchObject({
      eligible: false,
      label: 'USB inventory stale',
    });
  });

  it('uses the audited 30-second tile and 2.5-second sheet cadence', async () => {
    const view = render(<StorageProbe sheetOpen={false} />);
    await act(async () => Promise.resolve());
    expect(fetchDiskStatusMock).toHaveBeenCalledTimes(1);
    expect(fetchStoragePolicyMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(STORAGE_TILE_POLL_INTERVAL_MS);
    });
    expect(fetchDiskStatusMock).toHaveBeenCalledTimes(2);

    view.rerender(<StorageProbe sheetOpen />);
    await act(async () => Promise.resolve());
    expect(fetchDiskStatusMock).toHaveBeenCalledTimes(3);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(STORAGE_SHEET_POLL_INTERVAL_MS);
    });
    expect(fetchDiskStatusMock).toHaveBeenCalledTimes(4);
  });

  it('renders policy, health, and reset explanation without mutation controls', () => {
    const resources = {
      policy: resource(sampleStoragePolicy()),
      disks: resource(sampleDiskStatus()),
    };
    const controls = storageControls();
    const sharedUsbControls = usbControls();
    render(
      <>
        <StorageTile resources={resources} onOpen={vi.fn()} />
        <StorageSheet
          open
          onClose={vi.fn()}
          resources={resources}
          usbResource={resource(sampleUsbStatus())}
          controls={controls}
          usbControls={sharedUsbControls}
        />
      </>,
    );

    expect(
      screen.getByText('Currently mounted read/write; access checks pass'),
    ).toBeInTheDocument();
    expect(screen.getByText('Unmount first')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Unmount' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Repair' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Reset USB' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'HDDs enabled' }));
    expect(controls.setPolicy).toHaveBeenCalledWith('disks_enabled', false);
  });
});
