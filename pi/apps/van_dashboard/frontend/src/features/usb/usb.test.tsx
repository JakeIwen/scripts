import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { ToastProvider } from '../../components/ToastProvider';
import { fetchUsbStatus } from './api';
import type { UsbControls } from './controls';
import { decodeUsbStatusResponse } from './schema';
import { UsbStatusProvider, useSharedUsbStatus } from './hooks';
import { UsbSheet } from './UsbSheet';
import { UsbTile } from './UsbTile';
import { sampleUsbStatus, usbStatusPayload } from './testFixtures';

vi.mock('./api', () => ({ fetchUsbStatus: vi.fn() }));
const fetchUsbStatusMock = vi.mocked(fetchUsbStatus);

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

function SharedUsbConsumer({ label }: { label: string }) {
  const { resource } = useSharedUsbStatus();
  return (
    <output aria-label={label}>{resource.data?.inventory.presentDeviceCount ?? 'loading'}</output>
  );
}

function resourceWithData() {
  return {
    data: sampleUsbStatus(),
    error: null,
    initialLoading: false,
    refreshing: false,
    lastUpdatedAt: Date.now(),
    refresh: vi.fn().mockResolvedValue(sampleUsbStatus()),
  };
}

function controlMocks(): UsbControls {
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

describe('USB feature', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    fetchUsbStatusMock.mockResolvedValue(sampleUsbStatus());
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('strictly decodes inventory and cached port topology', () => {
    const status = decodeUsbStatusResponse(usbStatusPayload());
    expect(status.inventory.storageLabels).toEqual(['movingparts']);
    expect(status.ports.hubs[0]?.ports[0]).toMatchObject({
      key: '2-2:1',
      method: 'power',
      storageLabels: ['movingparts'],
    });
  });

  it('shares one polled snapshot with USB and storage consumers', async () => {
    render(
      <ToastProvider>
        <UsbStatusProvider>
          <SharedUsbConsumer label="USB consumer" />
          <SharedUsbConsumer label="Storage consumer" />
        </UsbStatusProvider>
      </ToastProvider>,
    );
    await act(async () => Promise.resolve());

    expect(fetchUsbStatusMock).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText('USB consumer')).toHaveTextContent('1');
    expect(screen.getByLabelText('Storage consumer')).toHaveTextContent('1');
  });

  it('renders guarded cached controls', () => {
    const resource = resourceWithData();
    resource.data.inventory.lastSuccessAt = Date.now() / 1000 - 3;
    const controls = controlMocks();
    render(
      <>
        <UsbTile resource={resource} onOpen={vi.fn()} />
        <UsbSheet open onClose={vi.fn()} resource={resource} controls={controls} />
      </>,
    );

    expect(screen.getByRole('button', { name: 'Cycle' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Disable' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Enable' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Recover USB 2' })).toBeEnabled();
    expect(resource.refresh).not.toHaveBeenCalled();
  });
});
