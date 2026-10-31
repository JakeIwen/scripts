import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import type { ICloudAttempt } from './icloud';
import { UploadSpeedButton } from './UploadSpeedSheet';
import { linearTrend, rollingMean, speedStats, splitRuns, type SpeedSample } from './uploadSpeeds';

const NOW = 1_800_000_000;
const MIB = 1024 * 1024;

function minutes(start: number, speeds: number[]): SpeedSample[] {
  return speeds.map((speed, index) => ({ at: start + (index + 1) * 60, speed }));
}

beforeAll(() => {
  if (!HTMLDialogElement.prototype.showModal) {
    HTMLDialogElement.prototype.showModal = function () {
      this.setAttribute('open', '');
    };
  }
  if (!HTMLDialogElement.prototype.close) {
    HTMLDialogElement.prototype.close = function () {
      this.removeAttribute('open');
      this.dispatchEvent(new Event('close'));
    };
  }
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('upload speed analysis', () => {
  it('splits runs at pauses longer than five minutes', () => {
    const samples = [
      ...minutes(NOW, [1, 2, 3]),
      ...minutes(NOW + 180 + 240, [4]),
      ...minutes(NOW + 3600, [5, 6]),
    ];
    const runs = splitRuns(samples);
    expect(runs.map((run) => run.samples.length)).toEqual([4, 2]);
    expect(runs[0]?.start).toBe(NOW);
    expect(runs[1]?.end).toBe(NOW + 3600 + 120);
  });

  it('summarizes average, median, peak, uploading time and data sent', () => {
    expect(speedStats(minutes(NOW, [100, 300, 200, 400]))).toEqual({
      average: 250,
      median: 250,
      peak: 400,
      uploadingSeconds: 240,
      sentBytes: 60_000,
    });
    expect(speedStats([])).toBeNull();
  });

  it('fits a least-squares trend and a trailing rolling mean', () => {
    const rising = minutes(NOW, [10, 20, 30, 40]);
    const trend = linearTrend(rising);
    expect(trend?.slope).toBeCloseTo(10 / 60);
    expect(linearTrend(rising.slice(0, 2))).toBeNull();
    expect(rollingMean(rising, 2).map((point) => point.speed)).toEqual([10, 15, 25, 35]);
  });
});

describe('upload speed sheet', () => {
  const attempt: ICloudAttempt = {
    id: 'a'.repeat(32),
    startedAt: NOW - 86_400 - 60,
    endedAt: NOW - 86_400 + 600,
    phase: 'complete',
    workPhase: 'complete',
    generation: null,
    message: 'Verified.',
    progress: {
      uploadEstimatedBytes: null,
      uploadTotalBytes: null,
      commandBytes: null,
      verifiedBytes: null,
      verificationTotalBytes: null,
      verifiedFiles: null,
      verificationTotalFiles: null,
      currentFileBytes: null,
    },
    verifiedAt: null,
  };

  function stat(label: string) {
    const rows = [...document.querySelectorAll('.upload-speed__stats > div')];
    const row = rows.find(
      (element) => element.querySelector('dt')?.firstChild?.textContent === label,
    );
    return row?.querySelector('dd')?.textContent;
  }

  function open(samples: number[][]) {
    vi.spyOn(Date, 'now').mockReturnValue(NOW * 1000);
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true, samples }), {
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);
    render(
      <UploadSpeedButton kind="time-machine" title="Mac" attempts={[attempt]} running={false} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Upload speed graph' }));
    return fetchMock;
  }

  it('loads the job’s samples and focuses the latest run, then any chosen run', async () => {
    const older = minutes(NOW - 86_400, [MIB, MIB, MIB]);
    const latest = minutes(NOW - 3600, [3 * MIB, 3 * MIB, 3 * MIB]);
    const fetchMock = open([...older, ...latest].map((s) => [s.at, s.speed]));
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain(
      '/api/backups/cloud/time-machine/upload-speeds',
    );
    await waitFor(() => expect(screen.getByText('Selected run · minute by minute')).toBeTruthy());
    const average = () => stat('Average');
    expect(average()).toBe('3.0 MiB/s');
    expect(stat('Vs 30 days')).toBe('+50%');

    const table = screen.getByRole('table');
    const rows = within(table).getAllByRole('row');
    expect(rows).toHaveLength(3);
    expect(within(rows[2] as HTMLElement).getByText('Verified')).toBeTruthy();
    fireEvent.click(within(rows[2] as HTMLElement).getByRole('button'));
    expect(average()).toBe('1.0 MiB/s');
    expect(stat('Vs 30 days')).toBe('-50%');
    expect(stat('Attempt result')).toBe('Verified');

    fireEvent.click(screen.getByRole('button', { name: 'All 30 days' }));
    expect(average()).toBe('2.0 MiB/s');
    expect(screen.queryByText('Selected run · minute by minute')).toBeNull();
    fireEvent.click(screen.getAllByRole('button', { name: /average$/ })[1] as HTMLElement);
    expect(average()).toBe('3.0 MiB/s');
  });

  it('explains an empty month instead of drawing an empty chart', async () => {
    open([]);
    expect(await screen.findByText(/No uploads in the last 30 days/)).toBeTruthy();
    expect(screen.queryByRole('img')).toBeNull();
  });
});
