import { cleanup, fireEvent, render, screen } from '@testing-library/react';
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
  it('shows verification catching up independently while uploading, and retains saved checks when paused', () => {
    const status = cloud();
    Object.assign(status, { running: true, phase: 'uploading', updatedAt: Date.now() / 1000 });
    Object.assign(status.progress, {
      parallelVerification: true,
      verifiedBytes: 100,
      verificationTotalBytes: 1000,
      verifiedFiles: 2,
      verificationTotalFiles: 20,
      verificationBytesPerSecond: 1024,
      verificationUpdatedAt: Date.now() / 1000,
    });
    const view = render(<ICloudBackupCard status={status} kind="time-machine" />);
    expect(screen.getByRole('progressbar', { name: 'iCloud upload estimate' })).toHaveAttribute(
      'aria-valuenow',
      '75',
    );
    expect(
      screen.getByRole('progressbar', { name: 'Concurrent iCloud download verification' }),
    ).toHaveAttribute('aria-valuenow', '10');
    expect(screen.getByText(/Download verification.*10.0%/)).toHaveTextContent('1.0 KiB/s');
    view.rerender(
      <ICloudBackupCard
        status={{ ...status, running: false, phase: 'paused' }}
        kind="time-machine"
      />,
    );
    expect(screen.getByText(/Saved verification.*10.0%/)).not.toHaveTextContent('KiB/s');
    view.rerender(
      <ICloudBackupCard
        status={{
          ...status,
          progress: {
            ...status.progress,
            verificationWaiting: true,
            verificationBytesPerSecond: null,
          },
        }}
        kind="time-machine"
      />,
    );
    expect(screen.getByText(/Caught up with completed uploads/)).toBeInTheDocument();
  });

  it('identifies verification waits and labels its effective rate', () => {
    const status = cloud();
    Object.assign(status, { running: true, phase: 'uploading' });
    Object.assign(status.progress, {
      parallelVerification: true,
      verificationTotalBytes: 1000,
      verifiedBytes: 100,
      verificationUpdatedAt: Date.now() / 1000,
      verificationBytesPerSecond: 1024,
      verificationActivity: 'network-check',
    });
    const view = render(<ICloudBackupCard status={status} kind="time-machine" />);
    expect(screen.getByText(/Checking network route/)).toBeInTheDocument();
    expect(screen.getByText(/1.0 KiB\/s average/)).toHaveAttribute(
      'title',
      expect.stringContaining('including connection'),
    );
    view.rerender(
      <ICloudBackupCard
        status={{ ...status, progress: { ...status.progress, verificationActivity: 'connecting' } }}
        kind="time-machine"
      />,
    );
    expect(screen.getByText(/Waiting for iCloud data/)).toBeInTheDocument();
    view.rerender(
      <ICloudBackupCard status={{ ...status, progressStale: true }} kind="time-machine" />,
    );
    expect(screen.queryByText(/Checking network route/)).not.toBeInTheDocument();
    expect(screen.queryByText(/KiB\/s average/)).not.toBeInTheDocument();
  });

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
    const view = render(<ICloudBackupCard status={status} />);
    expect(screen.getByText(/2h 23m left/)).toBeInTheDocument();
    expect(screen.getByText(/232 MiB of 241 MiB.*96.6%/)).toBeInTheDocument();
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
    view.rerender(<ICloudBackupCard status={{ ...status, stalled: true }} />);
    expect(screen.queryByText(/2h 23m left/)).not.toBeInTheDocument();
  });

  it('highlights paused and stalled cloud rows while another backup is running', () => {
    const data = sampleBackupStatus(true);
    data.timeMachineIcloud = { ...cloud(), phase: 'paused', manualPauseIndefinite: true };
    data.icloud = { ...cloud(), running: true, phase: 'uploading', stalled: true };
    render(
      <BackupsTile
        resource={{
          data,
          error: null,
          initialLoading: false,
          refreshing: false,
          lastUpdatedAt: Date.now(),
          refresh: vi.fn(),
        }}
        onOpen={vi.fn()}
      />,
    );
    expect(screen.getByText('Mac · iCloud').parentElement).toHaveClass(
      'backups-tile__status-line--paused',
    );
    expect(screen.getByText('Pi · iCloud').parentElement).toHaveClass(
      'backups-tile__status-line--stalled',
    );
    expect(screen.getByText('Stalled · 75.0%')).toBeInTheDocument();
    expect(screen.getByText('Manually paused · 75.0%')).toBeInTheDocument();
  });

  it('offers indefinite pause, keeps Resume help in its tooltip and requests a safe switch', async () => {
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(
        async () =>
          new Response(JSON.stringify({ ok: true, message: 'Accepted' }), { status: 202 }),
      );
    const status = { ...cloud(), phase: 'paused' as const, manualPauseIndefinite: true };
    render(
      <ICloudBackupControls
        kind="time-machine"
        status={status}
        other={{ ...cloud(), running: true }}
        refresh={vi.fn()}
        blocked={false}
      />,
    );
    const help =
      'Resume retries saved work when the backup lock, disk, ignition and network checks allow it.';
    expect(screen.getByRole('button', { name: 'Resume now' })).toHaveAttribute('title', help);
    expect(screen.queryByText(help)).not.toBeInTheDocument();
    expect(screen.getByText(/Paused indefinitely/)).toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'indefinite' } });
    fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
    await screen.findByRole('status');
    expect(String(fetch.mock.calls[0]?.[1]?.body)).toBe('minutes=indefinite');
    fireEvent.click(screen.getByRole('button', { name: 'Prioritize & run' }));
    await screen.findByRole('status');
    expect(fetch.mock.calls[1]?.[0]).toBe('/api/backups/time-machine-icloud/take-turn');
    expect(String(fetch.mock.calls[1]?.[1]?.body)).toBe('');
  });

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
    expect(screen.queryByText(/normal Time Machine backups resume/i)).not.toBeInTheDocument();
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
    'shows percent, current speed and time left together for a fresh %s upload',
    (kind) => {
      const status = cloud();
      Object.assign(status, { running: true, phase: 'uploading', updatedAt: Date.now() / 1000 });
      const speed = 2.2 * 1024 ** 2;
      Object.assign(status.progress, {
        uploadBytesPerSecond: speed,
        uploadTotalBytes: 403 * 1024 ** 3,
        uploadEstimatedBytes: 20 * 1024 ** 3,
      });
      const view = render(<ICloudBackupCard status={status} kind={kind} />);
      const headline = screen.getByText(/20 GiB of 403 GiB/);
      expect(headline).toHaveTextContent('20 GiB of 403 GiB · 5.0% · 2.2 MiB/s · 2d 1h 32m left');
      expect(
        screen.queryByText(/includes files saved by earlier attempts/),
      ).not.toBeInTheDocument();
      expect(screen.queryByText(/Upload speed:/)).not.toBeInTheDocument();
      view.rerender(<ICloudBackupCard status={{ ...status, progressStale: true }} kind={kind} />);
      expect(headline).toHaveTextContent('20 GiB of 403 GiB · 5.0% · measuring speed…');
      expect(screen.queryByText(/2.2 MiB\/s|2d 1h 32m left/)).not.toBeInTheDocument();
      view.rerender(<ICloudBackupCard status={{ ...status, running: false }} kind={kind} />);
      expect(screen.queryByText(/MiB\/s|measuring speed/)).not.toBeInTheDocument();
    },
  );

  it.each(['pi', 'time-machine'] as const)(
    'sends the selected %s pause and resume',
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
      fireEvent.change(screen.getByRole('combobox', { name: 'Pause for' }), {
        target: { value: '240' },
      });
      fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
      expect(await screen.findByRole('status')).toHaveTextContent('Accepted');
      expect(fetch.mock.calls[0]?.[0]).toBe(`/api/backups/${path}/pause`);
      expect(String(fetch.mock.calls[0]?.[1]?.body)).toBe('minutes=240');
      expect(refresh).toHaveBeenCalledTimes(1);
      fetch.mockResolvedValue(
        new Response(JSON.stringify({ ok: true, message: 'Resumed' }), { status: 202 }),
      );
      fireEvent.click(screen.getByRole('button', { name: 'Resume now' }));
      expect(await screen.findByText('Resumed')).toBeInTheDocument();
      expect(fetch.mock.calls[1]?.[0]).toBe(`/api/backups/${path}/resume`);
      expect(String(fetch.mock.calls[1]?.[1]?.body)).toBe('');
      fetch.mockRestore();
    },
  );

  it('shows a manual deadline and allows a running upload to be paused', () => {
    const status = { ...cloud(), running: true, manualPauseUntil: 1800003600 };
    render(<ICloudBackupControls kind="pi" status={status} refresh={vi.fn()} blocked={false} />);
    expect(screen.getByText(/Automatic resume/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Pause' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Resume now' })).toBeEnabled();
  });
});
