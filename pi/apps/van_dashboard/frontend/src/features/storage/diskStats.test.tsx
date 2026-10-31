import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import { segments } from '../../components/charts/TimeSeriesChart';
import { fetchDiskHistory } from './api';
import { historySeries, historySummary, smartVerdict } from './diskStats';
import { DiskStatsStrip } from './DiskStatsStrip';
import { sampleDiskHistory, sampleDiskStats, sampleDiskStatus } from './testFixtures';

vi.mock('./api', () => ({ fetchDiskHistory: vi.fn() }));
const fetchDiskHistoryMock = vi.mocked(fetchDiskHistory);
const MIB = 1024 * 1024;

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

afterEach(() => cleanup());

describe('disk history math', () => {
  it('weights averages by recorded minutes and readings', () => {
    const summary = historySummary(sampleDiskHistory());
    expect(summary).toMatchObject({
      recordedSeconds: 7200,
      readBytes: 10_800 * MIB,
      averageRead: 1.5 * MIB,
      peakRead: 8 * MIB,
      averageBusy: 10,
      peakBusy: 30,
      temperature: { low: 40, average: 41, high: 45 },
    });
  });

  it('plots bucket rates at midpoints and breaks lines across missing buckets', () => {
    const series = historySeries(sampleDiskHistory());
    expect(series.read.map((point) => point.value)).toEqual([MIB, 4 * MIB, 0]);
    expect(series.read[0]?.at).toBe(1_700_002_800 + 1800);
    expect(segments(series.read, 3601).map((run) => run.length)).toEqual([2, 1]);
  });

  it('names failure signals in the SMART verdict', () => {
    const smart = sampleDiskHistory().smart;
    if (!smart) throw new Error('missing SMART fixture');
    expect(smartVerdict(smart)).toEqual({
      tone: 'warning',
      text: 'SMART passed · 32 reallocated sectors',
    });
    expect(smartVerdict({ ...smart, reallocatedSectors: 0 }).tone).toBe('good');
    expect(smartVerdict({ ...smart, passed: false }).tone).toBe('bad');
  });
});

describe('disk history sheet', () => {
  it('loads the chosen range and draws I/O, busy and temperature', async () => {
    fetchDiskHistoryMock.mockResolvedValue(sampleDiskHistory());
    const disk = sampleDiskStatus().disks[0];
    if (!disk) throw new Error('missing disk fixture');
    render(<DiskStatsStrip disk={disk} stats={sampleDiskStats()} />);

    await act(async () => {
      fireEvent.click(
        screen.getByRole('button', { name: 'movingparts I/O and temperature history' }),
      );
    });
    expect(fetchDiskHistoryMock).toHaveBeenLastCalledWith(
      'movingparts',
      24,
      expect.any(AbortSignal),
    );
    expect(screen.getByRole('img', { name: 'Read and write rate over time' })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Share of time with I/O queued' })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Drive temperature over time' })).toBeInTheDocument();
    expect(screen.getByText('1.5 MiB/s')).toBeInTheDocument();
    expect(screen.getByText('12,345 h')).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '7 days' }));
    });
    expect(fetchDiskHistoryMock).toHaveBeenLastCalledWith(
      'movingparts',
      168,
      expect.any(AbortSignal),
    );
    expect(screen.getByRole('button', { name: '7 days' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('explains an empty range instead of drawing blank charts', async () => {
    fetchDiskHistoryMock.mockResolvedValue({
      ...sampleDiskHistory(),
      io: [],
      temperature: [],
      smart: null,
    });
    const disk = sampleDiskStatus().disks[0];
    if (!disk) throw new Error('missing disk fixture');
    render(<DiskStatsStrip disk={disk} stats={null} />);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /history/i }));
    });
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
    expect(screen.getByText(/No I\/O recorded/)).toBeInTheDocument();
    expect(screen.getByText(/parked disks are\s+never woken/)).toBeInTheDocument();
  });
});
