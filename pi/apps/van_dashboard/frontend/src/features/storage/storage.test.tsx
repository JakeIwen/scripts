import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { fetchDiskStatus, fetchStoragePolicy } from './api';
import type { StorageControls } from './controls';
import { decodeDiskStatusResponse, decodeStoragePolicyResponse } from './schema';
import { assessUsbReset } from './resetEligibility';
import { StorageSheet } from './StorageSheet';
import { StorageTile } from './StorageTile';
import {
  diskStatusPayload,
  sampleDiskStats,
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
    cleanup();
    vi.useRealTimers();
  });

  it('strictly decodes policy runtime and disk health evidence', () => {
    const policy = decodeStoragePolicyResponse(storagePolicyPayload());
    const disks = decodeDiskStatusResponse(diskStatusPayload());
    expect(policy.allowStarlinkTorrents).toBe(false);
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

  it('enables disk actions, blocks USB reset for a mounted disk, and wires the policy toggle', () => {
    const resources = {
      policy: resource(sampleStoragePolicy()),
      disks: resource(sampleDiskStatus()),
      stats: resource(sampleDiskStats()),
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
      screen.queryByRole('button', { name: /Allow torrents on Starlink/i }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Torrents enabled' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Unmount' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Repair' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Reset USB' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'HDDs enabled' }));
    expect(controls.setPolicy).toHaveBeenCalledWith('disks_enabled', false);

    // The fresh I/O minute, SMART reading and mounted space all reach the card.
    expect(screen.getByText('3.0 MiB/s')).toBeInTheDocument();
    expect(screen.getByText('512 KiB/s')).toBeInTheDocument();
    expect(screen.getByText('43%')).toBeInTheDocument();
    expect(screen.getByText('41 °C')).toBeInTheDocument();
    expect(screen.getByText('1.2 TiB')).toBeInTheDocument();
    expect(
      screen.getByText('SMART passed · 32 reallocated sectors · 12,345 h powered on'),
    ).toBeInTheDocument();
  });

  it('shows dashes once a disk stops reporting I/O', () => {
    const stats = sampleDiskStats();
    const resources = {
      policy: resource(sampleStoragePolicy()),
      disks: resource(sampleDiskStatus()),
      stats: resource({ ...stats, checkedAt: stats.checkedAt + 600 }),
    };
    render(
      <StorageSheet
        open
        onClose={vi.fn()}
        resources={resources}
        usbResource={resource(sampleUsbStatus())}
        controls={storageControls()}
        usbControls={usbControls()}
      />,
    );
    expect(screen.getAllByText('no recent sample')).toHaveLength(3);
    expect(screen.getByText('41 °C')).toBeInTheDocument();
  });
});
