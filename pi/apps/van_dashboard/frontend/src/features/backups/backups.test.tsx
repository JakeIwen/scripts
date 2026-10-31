import { ToastProvider } from '../../components/ToastProvider';
import type { ReactNode } from 'react';
import {
  cleanup,
  fireEvent,
  render as testingRender,
  screen,
  within,
} from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { fetchBackupStatus } from './api';
import type { BackupControls } from './controls';
import { BackupsSheet } from './BackupsSheet';
import { BackupsTile } from './BackupsTile';
import { decodeBackupStatusResponse } from './decoders';
import {
  backupStatusPayload,
  sampleBackupStatus,
  sampleRunningICloudStatus,
  backupPreferencesPayload,
} from './testFixtures';

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

function render(ui: ReactNode) {
  return testingRender(ui, { wrapper: ToastProvider });
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
    setCloneCardNominalGb: vi.fn().mockResolvedValue(undefined),
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

  it('explains CHECK on tap without opening backup controls', () => {
    const resource = backupResource();
    const cloud = sampleRunningICloudStatus();
    resource.data.timeMachineIcloud = {
      ...cloud,
      running: false,
      phase: 'deferred',
      attention: true,
      message: 'Waiting for a permitted internet route.',
    };
    const onOpen = vi.fn();
    render(<BackupsTile resource={resource} onOpen={onOpen} />);
    fireEvent.click(screen.getByRole('button', { name: 'Backup status: CHECK' }));
    expect(screen.getByRole('tooltip')).toHaveTextContent('EXFAT512: last success');
    expect(screen.getByRole('tooltip')).toHaveTextContent(
      'Mac · iCloud: Waiting for a permitted internet route.',
    );
    expect(onOpen).not.toHaveBeenCalled();
    fireEvent.keyDown(screen.getByRole('button', { name: 'Backup status: CHECK' }), {
      key: 'Escape',
    });
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Open backup details' }));
    expect(onOpen).toHaveBeenCalledOnce();
  });

  it('strictly decodes freshness, progress, hotspares, and Time Machine evidence', () => {
    const status = decodeBackupStatusResponse(backupStatusPayload(true));
    expect(status).toMatchObject({ health: 'running' });
    expect(status.borg.progress).toMatchObject({
      phase: 'borg_create',
      progressPercent: 42,
      bytesProcessed: 1_073_741_824,
    });
    expect(status.hotswaps[0]).toMatchObject({
      label: 'hotspare-a',
      attached: true,
      usedBytes: 21_474_836_480,
    });
    expect(status.settings).toEqual({
      cloneCardNominalGb: 64,
      rootUsedMaxGib: 52,
      cloneCardNominalGbOptions: [32, 64, 128, 256],
    });
  });

  it('disables another Run now while a dashboard backup operation is running', () => {
    const resource = backupResource(true);
    resource.data.checkedAt = Date.now() / 1000 - 3;
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
    const borgCard = screen.getByRole('heading', { name: 'Vanpi Borg' }).closest('article');
    const exfatCard = screen.getByRole('heading', { name: 'EXFAT512 snapshot' }).closest('article');
    if (!borgCard || !exfatCard) throw new Error('missing local backup cards');
    expect(within(borgCard).getByRole('button', { name: 'Stop gracefully' })).toBeEnabled();
    expect(within(exfatCard).getByRole('button', { name: 'Run now' })).toBeDisabled();
    for (const button of screen.getAllByRole('button', { name: 'Clone now' })) {
      expect(button).toBeDisabled();
    }
  });

  it('keeps Borg Run now enabled while scheduler jobs of other kinds are running', () => {
    const data = sampleBackupStatus();
    data.exfatSnapshot.running = true;
    data.openwrt.running = true;
    data.icloud = sampleRunningICloudStatus();
    data.timeMachineIcloud = sampleRunningICloudStatus();
    data.timeMachine.running = true;
    const resource = {
      ...backupResource(),
      data,
      refresh: vi.fn().mockResolvedValue(data),
    };
    render(<BackupsSheet open onClose={vi.fn()} resource={resource} controls={controlMocks()} />);

    const borgCard = screen.getByRole('heading', { name: 'Vanpi Borg' }).closest('article');
    if (!borgCard) throw new Error('missing Borg backup card');
    expect(within(borgCard).getByRole('button', { name: 'Run now' })).toBeEnabled();
  });

  it('keeps clone available while local, OpenWrt, and cloud backups are running', () => {
    const data = sampleBackupStatus();
    data.borg.running = true;
    data.exfatSnapshot.running = true;
    data.openwrt.running = true;
    data.icloud = sampleRunningICloudStatus();
    data.timeMachineIcloud = sampleRunningICloudStatus();
    const resource = {
      ...backupResource(),
      data,
      refresh: vi.fn().mockResolvedValue(data),
    };
    render(<BackupsSheet open onClose={vi.fn()} resource={resource} controls={controlMocks()} />);

    expect(screen.getByRole('button', { name: 'Clone now' })).toBeEnabled();
  });

  it('selects a persisted bootable clone-card capacity inside settings', async () => {
    vi.useRealTimers();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify(backupPreferencesPayload()))),
    );
    const resource = backupResource();
    const controls = controlMocks();
    render(<BackupsSheet open onClose={vi.fn()} resource={resource} controls={controls} />);

    fireEvent.click(screen.getByRole('button', { name: 'Backup settings' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Hotspares' }));
    const select = screen.getByRole('combobox', { name: 'Bootable clone card capacity' });
    expect(select).toHaveValue('64');
    expect(
      screen.getAllByRole('option').map((option) => (option as HTMLOptionElement).value),
    ).toEqual(['32', '64', '128', '256']);

    fireEvent.change(select, { target: { value: '128' } });
    expect(controls.setCloneCardNominalGb).toHaveBeenCalledWith(128);
    vi.unstubAllGlobals();
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
