import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ICloudBackupCard } from './ICloudBackupCard';
import { BackupsTile } from './BackupsTile';
import { decodeICloud, type AvailableICloudStatus } from './icloud';
import { sampleBackupStatus } from './testFixtures';
import { TimeMachineCloudControls } from './TimeMachineCloudControls';

const generation = 'vanpi-20260920T225254Z-ebf57e14';
function cloud(): AvailableICloudStatus {
  return {
    available: true,
    running: false,
    phase: 'deferred',
    message: 'Waiting for the daily local backups.',
    lastWorkPhase: 'uploading',
    generation,
    lastSuccessAt: null,
    nextCheckAt: 1800000300,
    nextDueAt: null,
    updatedAt: 1800000000,
    progressStale: false,
    attention: true,
    intervalDays: 7,
    keepGenerations: 8,
    historyStartedAt: 1800000000,
    progress: {
      uploadEstimatedBytes: 750,
      uploadTotalBytes: 1000,
      commandBytes: 50,
      verifiedBytes: null,
      verificationTotalBytes: null,
      verifiedFiles: null,
      verificationTotalFiles: null,
      currentFileBytes: null,
    },
    attempts: [],
    verifiedGenerations: [],
  };
}

afterEach(cleanup);
describe('iCloud dashboard', () => {
  it('tracks the Mac frozen capture separately from Pi upload progress', () => {
    const status = cloud();
    Object.assign(status, {
      running: true,
      phase: 'preparing',
      message: 'Capturing a frozen encrypted Time Machine image.',
    });
    Object.assign(status.progress, { captureBytes: 250, captureTotalBytes: 1000 });
    render(<ICloudBackupCard status={status} kind="time-machine" />);
    expect(screen.getByRole('heading', { name: 'Mac Time Machine · iCloud' })).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: 'Time Machine frozen copy' })).toHaveAttribute(
      'aria-valuenow',
      '25',
    );
    expect(screen.getByText(/normal Time Machine backups resume/i)).toBeInTheDocument();
    expect(screen.queryByText(/Upload percentage is size-based/)).not.toBeInTheDocument();
  });
  it('shows paused saved progress without claiming a verified recovery point', () => {
    render(<ICloudBackupCard status={cloud()} />);
    expect(screen.getByText('Waiting to retry')).toBeInTheDocument();
    expect(screen.getByText('No verified offsite backup yet.')).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: 'iCloud upload estimate' })).toHaveAttribute(
      'aria-valuenow',
      '75',
    );
    expect(screen.getByText(/Last saved progress/)).toBeInTheDocument();
    expect(
      screen.getByText(/Mac Time Machine replication is tracked separately/),
    ).toBeInTheDocument();
  });

  it('distinguishes verified files from the current unverified stream', () => {
    const status = cloud();
    Object.assign(status, {
      running: true,
      phase: 'verifying',
      message: 'Downloading and checking.',
      progressStale: true,
    });
    Object.assign(status.progress, {
      verifiedBytes: 500,
      verificationTotalBytes: 1000,
      verifiedFiles: 4,
      verificationTotalFiles: 6,
      currentFileBytes: 50,
    });
    render(<ICloudBackupCard status={status} />);
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '50');
    expect(screen.getByText(/4\/6 files verified/)).toBeInTheDocument();
    expect(screen.getByText(/credited after its checksum passes/)).toBeInTheDocument();
    expect(screen.getByText(/waiting for fresh counters/)).toBeInTheDocument();
  });

  it('renders durable attempts and verified-generation history', () => {
    const status = cloud();
    Object.assign(status, { phase: 'not due', lastSuccessAt: 1800000000, attention: false });
    status.attempts = [
      {
        id: '1',
        phase: 'complete',
        workPhase: 'retention',
        message: 'Recovery copy verified.',
        generation,
        startedAt: 1799990000,
        endedAt: 1800000000,
        verifiedAt: 1800000000,
        progress: status.progress,
      },
    ];
    status.verifiedGenerations = [{ generation, completedAt: 1800000000 }];
    render(<ICloudBackupCard status={status} />);
    fireEvent.click(screen.getByText('Attempt history (1)'));
    expect(screen.getByText('Recovery copy verified.')).toBeInTheDocument();
    expect(screen.getByText('Verified recovery history (1)')).toBeInTheDocument();
    expect(screen.queryByText('No verified offsite backup yet.')).not.toBeInTheDocument();
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  });

  it('keeps a paused job out of the tile running badge', () => {
    const status = sampleBackupStatus();
    status.icloud = cloud();
    render(
      <BackupsTile
        resource={{
          data: status,
          error: null,
          initialLoading: false,
          refreshing: false,
          lastUpdatedAt: Date.now(),
          refresh: vi.fn(),
        }}
        onOpen={vi.fn()}
      />,
    );
    expect(screen.getByText('Waiting to retry · 75.0%')).toBeInTheDocument();
    expect(screen.queryByText('RUNNING')).not.toBeInTheDocument();
  });

  it('isolates an unavailable helper and supports old API responses', () => {
    expect(decodeICloud(undefined)).toBeNull();
    expect(decodeICloud({ available: false })).toEqual({ available: false, running: false });
    render(<ICloudBackupCard status={{ available: false, running: false }} />);
    expect(screen.getByText(/Other backups are still shown/)).toBeInTheDocument();
  });

  it('rejects malformed counters and phases', () => {
    expect(() => decodeICloud({ available: true, running: true, phase: 'perfect' })).toThrow(
      'Unsupported iCloud phase',
    );
    expect(() =>
      decodeICloud({
        available: true,
        running: true,
        phase: 'uploading',
        message: '',
        last_work_phase: 'uploading',
        generation: null,
        last_success_at: -5,
      }),
    ).toThrow('nonnegative');
  });

  it.each(['pi', 'time-machine'] as const)(
    'shows current speed only for a fresh %s upload',
    (kind) => {
      const status = cloud();
      Object.assign(status, { running: true, phase: 'uploading' });
      status.progress.uploadBytesPerSecond = 1024;
      const view = render(<ICloudBackupCard status={status} kind={kind} />);
      expect(screen.getByText('Upload speed: 1.0 KiB/s')).toBeInTheDocument();
      view.rerender(<ICloudBackupCard status={{ ...status, progressStale: true }} kind={kind} />);
      expect(screen.getByText(/Waiting for a fresh measurement/)).toBeInTheDocument();
      view.rerender(<ICloudBackupCard status={{ ...status, running: false }} kind={kind} />);
      expect(screen.queryByText(/Upload speed/)).not.toBeInTheDocument();
    },
  );

  it('sends the selected pause and resume, and refreshes after each control', async () => {
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(
        new Response(JSON.stringify({ ok: true, message: 'Accepted' }), { status: 202 }),
      );
    const refresh = vi.fn().mockResolvedValue(null);
    const status = cloud();
    render(<TimeMachineCloudControls status={status} refresh={refresh} blocked={false} />);
    fireEvent.change(screen.getByRole('combobox', { name: 'Pause for' }), {
      target: { value: '240' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Accepted');
    expect(fetch.mock.calls[0]?.[0]).toBe('/api/backups/time-machine-icloud/pause');
    expect(String(fetch.mock.calls[0]?.[1]?.body)).toBe('minutes=240');
    expect(refresh).toHaveBeenCalledTimes(1);
    fetch.mockResolvedValue(
      new Response(JSON.stringify({ ok: true, message: 'Resumed' }), { status: 202 }),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Resume now' }));
    expect(await screen.findByText('Resumed')).toBeInTheDocument();
    expect(fetch.mock.calls[1]?.[0]).toBe('/api/backups/time-machine-icloud/resume');
    expect(String(fetch.mock.calls[1]?.[1]?.body)).toBe('');
    fetch.mockRestore();
  });

  it('shows a manual deadline and allows a running upload to be paused', () => {
    const status = { ...cloud(), running: true, manualPauseUntil: 1800003600 };
    render(<TimeMachineCloudControls status={status} refresh={vi.fn()} blocked={false} />);
    expect(screen.getByText(/Automatic resume/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Pause' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Resume now' })).toBeEnabled();
  });
});
