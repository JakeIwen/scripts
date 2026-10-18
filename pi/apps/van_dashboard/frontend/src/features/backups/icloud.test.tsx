import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ICloudBackupCard } from './ICloudBackupCard';
import { BackupsTile } from './BackupsTile';
import { decodeICloud, type AvailableICloudStatus } from './icloud';
import { sampleBackupStatus } from './testFixtures';
import { ICloudBackupControls } from './ICloudBackupControls';
import { iCloudUploadTimeLeft } from './cloudPresentation';

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

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
describe('iCloud dashboard', () => {
  it.each(['pi', 'time-machine'] as const)(
    'requests a fresh cloud copy explicitly for %s',
    async (kind) => {
      const fetch = vi.fn(
        async (_path: unknown, _init?: RequestInit) =>
          new Response(JSON.stringify({ ok: true, message: 'Backup queued' }), { status: 202 }),
      );
      vi.stubGlobal('fetch', fetch);
      const status = {
        ...cloud(),
        phase: 'not due' as const,
        controlsAvailable: true,
        lastSuccessAt: 1800000000,
        verifiedGenerations: [{ generation, completedAt: 1800000000 }],
      };
      const view = render(
        <ICloudBackupCard status={status} kind={kind} refresh={vi.fn().mockResolvedValue(null)} />,
      );
      fireEvent.click(screen.getByRole('button', { name: 'Back up now' }));
      expect(await screen.findByRole('status')).toHaveTextContent('Backup queued');
      expect(fetch.mock.calls[0]?.[0]).toBe(
        `/api/backups/${kind === 'pi' ? 'icloud' : 'time-machine-icloud'}/backup-now`,
      );
      view.rerender(
        <ICloudBackupCard
          status={{ ...status, backupNowPending: true }}
          kind={kind}
          refresh={vi.fn().mockResolvedValue(null)}
        />,
      );
      expect(screen.getByRole('button', { name: 'Resume now' })).toBeEnabled();
      vi.unstubAllGlobals();
    },
  );

  it('does not round an unfinished capture up to complete', () => {
    const status = cloud();
    Object.assign(status, { phase: 'error', lastWorkPhase: 'preparing' });
    Object.assign(status.progress, {
      captureBytes: 432546435316,
      captureTotalBytes: 432555930936,
      captureFiles: 8267,
      captureTotalFiles: 12904,
      captureComplete: false,
    });
    const view = render(<ICloudBackupCard status={status} kind="time-machine" />);
    expect(screen.getByText('Local frozen copy · 99.9%')).toBeInTheDocument();
    expect(screen.getByText(/8,267 \/ 12,904 files processed/)).toBeInTheDocument();
    expect(screen.getByText(/Capture not finalized yet/)).toBeInTheDocument();
    status.progress.captureBytes = status.progress.captureTotalBytes;
    view.rerender(<ICloudBackupCard status={{ ...status }} kind="time-machine" />);
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '99.9');
    Object.assign(status.progress, { captureFiles: 12904, captureComplete: true });
    view.rerender(<ICloudBackupCard status={{ ...status }} kind="time-machine" />);
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100');
    expect(screen.getByText(/Capture validated; ready for upload/)).toBeInTheDocument();
  });

  it('estimates remaining upload time only from fresh, moving upload counters', () => {
    const status = cloud();
    Object.assign(status, { running: true, phase: 'uploading', updatedAt: Date.now() / 1000 });
    Object.assign(status.progress, {
      uploadTotalBytes: 252352941,
      uploadEstimatedBytes: 243772941,
      uploadBytesPerSecond: 1000,
    });
    expect(iCloudUploadTimeLeft(status)).toBe('2h 23m');
    for (const changes of [
      { running: false },
      { progressStale: true },
      { stalled: true },
      { updatedAt: 1000 },
    ]) {
      expect(iCloudUploadTimeLeft({ ...status, ...changes })).toBeNull();
    }
    expect(iCloudUploadTimeLeft({ ...status, phase: 'verifying' })).toBeNull();
    expect(
      iCloudUploadTimeLeft({
        ...status,
        progress: { ...status.progress, uploadBytesPerSecond: 0 },
      }),
    ).toBeNull();
  });

  it('resumes the schedule for a verified copy without presenting its old progress as unfinished', async () => {
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(
        new Response(JSON.stringify({ ok: true, message: 'Accepted' }), { status: 202 }),
      );
    const status: AvailableICloudStatus = {
      ...cloud(),
      phase: 'paused',
      lastWorkPhase: 'verifying',
      manualPauseIndefinite: true,
      controlsAvailable: true,
      lastSuccessAt: 1800000000,
      nextDueAt: 1800604800,
      verifiedGenerations: [{ generation, completedAt: 1800000000 }],
      progress: {
        ...cloud().progress,
        verifiedBytes: 1000,
        verificationTotalBytes: 1000,
        verifiedFiles: 10,
        verificationTotalFiles: 10,
      },
    };
    const refresh = vi.fn().mockResolvedValue(null);
    const view = render(<ICloudBackupCard status={status} refresh={refresh} />);
    const resume = screen.getByRole('button', { name: 'Resume schedule' });
    expect(resume).toHaveAttribute(
      'title',
      expect.stringContaining('without requesting a new copy'),
    );
    expect(screen.getByText(/Last verified/)).toBeInTheDocument();
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    fireEvent.click(resume);
    expect(await screen.findByRole('status')).toHaveTextContent('Accepted');
    expect(fetch.mock.calls[0]?.[0]).toBe('/api/backups/icloud/resume');
    expect(String(fetch.mock.calls[0]?.[1]?.body)).toBe('');

    view.rerender(
      <ICloudBackupCard
        status={{ ...status, generation: 'vanpi-20261008T000000Z-12345678' }}
        refresh={refresh}
      />,
    );
    expect(screen.getByRole('button', { name: 'Resume backup' })).toBeInTheDocument();
    expect(
      screen.getByRole('progressbar', { name: 'iCloud download verification' }),
    ).toHaveAttribute('aria-valuenow', '100');
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
      screen.queryByText(/Mac Time Machine replication is tracked separately/),
    ).not.toBeInTheDocument();
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

  it.each(['pi', 'time-machine'] as const)(
    'applies a timed %s pause immediately, keeps the main Pause indefinite, and resumes',
    async (kind) => {
      const path = kind === 'pi' ? 'icloud' : 'time-machine-icloud';
      const fetch = vi
        .spyOn(globalThis, 'fetch')
        .mockResolvedValue(
          new Response(JSON.stringify({ ok: true, message: 'Accepted' }), { status: 202 }),
        );
      const refresh = vi.fn().mockResolvedValue(null);
      const status = cloud();
      render(
        <ICloudBackupControls kind={kind} status={status} refresh={refresh} blocked={false} />,
      );
      const duration = screen.getByRole('combobox', { name: 'Pause duration' });
      fireEvent.change(duration, {
        target: { value: '240' },
      });
      expect(await screen.findByRole('status')).toHaveTextContent('Accepted');
      expect(fetch.mock.calls[0]?.[0]).toBe(`/api/backups/${path}/pause`);
      expect(String(fetch.mock.calls[0]?.[1]?.body)).toBe('minutes=240');
      expect(refresh).toHaveBeenCalledTimes(1);
      expect(duration).toHaveValue('');
      fetch.mockResolvedValue(
        new Response(JSON.stringify({ ok: true, message: 'Paused indefinitely' }), { status: 202 }),
      );
      fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
      expect(await screen.findByRole('status')).toHaveTextContent('Paused indefinitely');
      expect(fetch.mock.calls[1]?.[0]).toBe(`/api/backups/${path}/pause`);
      expect(String(fetch.mock.calls[1]?.[1]?.body)).toBe('minutes=indefinite');
      expect(refresh).toHaveBeenCalledTimes(2);
      fetch.mockResolvedValue(
        new Response(JSON.stringify({ ok: true, message: 'Resumed' }), { status: 202 }),
      );
      fireEvent.click(screen.getByRole('button', { name: 'Resume now' }));
      expect(await screen.findByText('Resumed')).toBeInTheDocument();
      expect(fetch.mock.calls[2]?.[0]).toBe(`/api/backups/${path}/resume`);
      expect(String(fetch.mock.calls[2]?.[1]?.body)).toBe('');
      fetch.mockRestore();
    },
  );

  it('disables both pause halves while refreshing an action and when controls are blocked', async () => {
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(
        new Response(JSON.stringify({ ok: true, message: 'Accepted' }), { status: 202 }),
      );
    let finishRefresh = () => {};
    const refresh = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finishRefresh = resolve;
        }),
    );
    const status = cloud();
    const view = render(
      <ICloudBackupControls kind="pi" status={status} refresh={refresh} blocked={false} />,
    );
    const pause = screen.getByRole('button', { name: 'Pause' });
    const duration = screen.getByRole('combobox', { name: 'Pause duration' });
    fireEvent.click(pause);
    await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
    expect(pause).toBeDisabled();
    expect(duration).toBeDisabled();
    fireEvent.click(pause);
    expect(fetch).toHaveBeenCalledTimes(1);
    await act(async () => finishRefresh());
    expect(pause).toBeEnabled();
    expect(duration).toBeEnabled();
    view.rerender(<ICloudBackupControls kind="pi" status={status} refresh={refresh} blocked />);
    expect(pause).toBeDisabled();
    expect(duration).toBeDisabled();
  });
});
