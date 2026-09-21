import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ICloudBackupCard } from './ICloudBackupCard';
import { BackupsTile } from './BackupsTile';
import { decodeICloud, type AvailableICloudStatus } from './icloud';
import { sampleBackupStatus } from './testFixtures';

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
  it('shows paused saved progress without claiming a verified recovery point', () => {
    render(<ICloudBackupCard status={cloud()} />);
    expect(screen.getByText('Paused')).toBeInTheDocument();
    expect(screen.getByText('No verified offsite backup yet.')).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: 'iCloud upload estimate' })).toHaveAttribute(
      'aria-valuenow',
      '75',
    );
    expect(screen.getByText(/Last saved progress/)).toBeInTheDocument();
    expect(screen.getByText(/Mac Time Machine is not copied/)).toBeInTheDocument();
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
    expect(screen.getByText('Paused · 75.0%')).toBeInTheDocument();
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
});
