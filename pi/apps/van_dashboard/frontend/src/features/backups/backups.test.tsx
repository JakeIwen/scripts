import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { fetchBackupStatus } from './api';
import type { BackupControls } from './controls';
import { BackupsSheet } from './BackupsSheet';
import { BackupsTile } from './BackupsTile';
import { decodeBackupStatusResponse } from './decoders';
import {
  BACKUP_IDLE_SHEET_POLL_INTERVAL_MS,
  BACKUP_RUNNING_POLL_INTERVAL_MS,
  useBackupStatus,
} from './hooks';
import { backupStatusPayload, sampleBackupStatus } from './testFixtures';

vi.mock('./api', () => ({ fetchBackupStatus: vi.fn() }));
const fetchBackupStatusMock = vi.mocked(fetchBackupStatus);

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

function BackupProbe({ sheetOpen }: { sheetOpen: boolean }) {
  const resource = useBackupStatus(sheetOpen);
  return <output>{resource.data?.health ?? 'loading'}</output>;
}

function backupResource(running = false) {
  const data = sampleBackupStatus(running);
  return {
    data,
    error: null,
    initialLoading: false,
    refreshing: false,
    lastUpdatedAt: Date.now(),
    refresh: vi.fn().mockResolvedValue(data),
  };
}

function controlMocks(): BackupControls {
  return {
    running: false,
    blocked: false,
    uncertainOutcome: false,
    pendingOperation: null,
    pendingStop: null,
    lastMessage: null,
    lastError: null,
    start: vi.fn().mockResolvedValue(undefined),
    stop: vi.fn().mockResolvedValue(undefined),
    clone: vi.fn().mockResolvedValue(undefined),
    reconcile: vi.fn(),
  };
}

describe('backups feature', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    fetchBackupStatusMock.mockResolvedValue(sampleBackupStatus());
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('strictly decodes freshness, progress, hotspares, and Time Machine evidence', () => {
    const status = decodeBackupStatusResponse(backupStatusPayload(true));
    expect(status).toMatchObject({ health: 'running' });
    expect(status.borg.progress).toMatchObject({
      phase: 'borg_create',
      progressPercent: 42,
      bytesProcessed: 1_073_741_824,
    });
    expect(status.hotswaps[0]).toMatchObject({ label: 'hotspare-a', attached: true });
  });

  it('rejects unsupported aggregate health states', () => {
    const payload = backupStatusPayload();
    const backups = payload.backups as Record<string, unknown>;
    backups.health = 'perfect';
    expect(() => decodeBackupStatusResponse(payload)).toThrow(
      'backups.health has an unsupported value',
    );
  });

  it('uses 10-second idle and 2.5-second running sheet polling', async () => {
    fetchBackupStatusMock
      .mockResolvedValueOnce(sampleBackupStatus())
      .mockResolvedValue(sampleBackupStatus(true));
    render(<BackupProbe sheetOpen />);
    await act(async () => Promise.resolve());
    expect(fetchBackupStatusMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(BACKUP_IDLE_SHEET_POLL_INTERVAL_MS);
    });
    await act(async () => Promise.resolve());
    expect(fetchBackupStatusMock.mock.calls.length).toBeGreaterThanOrEqual(2);
    const afterRunningDetected = fetchBackupStatusMock.mock.calls.length;

    await act(async () => {
      await vi.advanceTimersByTimeAsync(BACKUP_RUNNING_POLL_INTERVAL_MS);
    });
    expect(fetchBackupStatusMock.mock.calls.length).toBe(afterRunningDetected + 1);
  });

  it('renders operation-aware start, stop, and clone controls', () => {
    const resource = backupResource(true);
    const controls = controlMocks();
    render(
      <>
        <BackupsTile resource={resource} onOpen={vi.fn()} />
        <BackupsSheet open onClose={vi.fn()} resource={resource} controls={controls} />
      </>,
    );

    expect(screen.getAllByText('Creating Borg archive').length).toBeGreaterThan(0);
    expect(screen.getByText('hotspare-a')).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: 'Vanpi Borg progress' })).toHaveAttribute(
      'aria-valuenow',
      '42',
    );
    expect(screen.getByRole('button', { name: 'Stop gracefully' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Run now' })).toBeDisabled();
    for (const button of screen.getAllByRole('button', { name: 'Clone now' })) {
      expect(button).toBeDisabled();
    }
  });

  it('shows an authoritative graceful-stop state and cleanup consequences', () => {
    const data = sampleBackupStatus(true);
    data.stop = {
      status: 'running',
      kind: 'borg',
      startedAt: 1_700_000_110,
      completedAt: null,
      error: null,
    };
    if (!data.borg.progress) throw new Error('missing Borg progress fixture');
    data.borg.progress.phase = 'cloning';
    const resource = {
      ...backupResource(true),
      data,
      refresh: vi.fn().mockResolvedValue(data),
    };
    render(<BackupsSheet open onClose={vi.fn()} resource={resource} controls={controlMocks()} />);

    expect(screen.getByText('Stopping gracefully…')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Stopping…' })).toBeDisabled();
    expect(screen.getByText(/in-progress hotspare may be incomplete/i)).toBeInTheDocument();
    for (const button of screen.getAllByRole('button', { name: 'Clone now' })) {
      expect(button).toBeDisabled();
    }
  });
});
