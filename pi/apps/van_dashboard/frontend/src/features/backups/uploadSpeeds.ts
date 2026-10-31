import { getJson } from '../../api/client';
import { arrayValue, numberValue, objectValue } from '../../api/validation';
import { formatBytes } from '../../utils/format';
import type { ICloudAttempt, ICloudBackupKind } from './icloud';

/** One per-minute sample from the Pi's 30-day log: epoch seconds, bytes per second. */
export interface SpeedSample {
  at: number;
  speed: number;
}

export interface SpeedRun {
  start: number;
  end: number;
  samples: SpeedSample[];
}

export interface SpeedStats {
  average: number;
  median: number;
  peak: number;
  uploadingSeconds: number;
  sentBytes: number;
}

export interface LinearTrend {
  slope: number;
  intercept: number;
}

export const SAMPLE_SECONDS = 60;
// The worker writes a sample every minute while uploading; a longer silence is
// a pause between attempts, so it starts a new run.
export const RUN_GAP_SECONDS = 5 * 60;
export const RETENTION_SECONDS = 30 * 86_400;
export const ROLLING_SAMPLES = 10;

export async function loadUploadSpeeds(
  kind: ICloudBackupKind,
  signal: AbortSignal,
): Promise<SpeedSample[]> {
  const response = objectValue(
    await getJson(`/api/backups/cloud/${kind}/upload-speeds`, signal),
    'upload speed response',
  );
  return arrayValue(response.samples, 'upload speed samples').map((value) => {
    const row = arrayValue(value, 'upload speed sample');
    if (row.length !== 2) throw new TypeError('upload speed sample must be a pair');
    const at = numberValue(row[0], 'sample time');
    const speed = numberValue(row[1], 'sample speed');
    if (at < 0 || speed < 0) throw new TypeError('upload speed sample must be nonnegative');
    return { at, speed };
  });
}

export function splitRuns(samples: SpeedSample[]): SpeedRun[] {
  const runs: SpeedRun[] = [];
  for (const sample of samples) {
    const current = runs.at(-1);
    if (current && sample.at - current.end <= RUN_GAP_SECONDS) {
      current.samples.push(sample);
      current.end = sample.at;
    } else {
      // A sample closes its minute, so the run began a minute earlier.
      runs.push({ start: sample.at - SAMPLE_SECONDS, end: sample.at, samples: [sample] });
    }
  }
  return runs;
}

export function speedStats(samples: SpeedSample[]): SpeedStats | null {
  if (samples.length === 0) return null;
  const speeds = samples.map((sample) => sample.speed).sort((a, b) => a - b);
  const middle = Math.floor(speeds.length / 2);
  const total = speeds.reduce((sum, speed) => sum + speed, 0);
  const at = (index: number) => speeds[index] ?? 0;
  return {
    average: total / speeds.length,
    median: speeds.length % 2 ? at(middle) : (at(middle - 1) + at(middle)) / 2,
    peak: at(speeds.length - 1),
    uploadingSeconds: speeds.length * SAMPLE_SECONDS,
    sentBytes: total * SAMPLE_SECONDS,
  };
}

/** Ordinary least squares fit of speed against time; null for fewer than 3 points. */
export function linearTrend(points: SpeedSample[]): LinearTrend | null {
  if (points.length < 3) return null;
  const meanAt = points.reduce((sum, point) => sum + point.at, 0) / points.length;
  const meanSpeed = points.reduce((sum, point) => sum + point.speed, 0) / points.length;
  let covariance = 0;
  let variance = 0;
  for (const point of points) {
    covariance += (point.at - meanAt) * (point.speed - meanSpeed);
    variance += (point.at - meanAt) ** 2;
  }
  if (variance === 0) return null;
  const slope = covariance / variance;
  return { slope, intercept: meanSpeed - slope * meanAt };
}

export function trendAt(trend: LinearTrend, at: number): number {
  return trend.intercept + trend.slope * at;
}

/** Trailing mean over the last `width` samples, so a value never uses later data. */
export function rollingMean(samples: SpeedSample[], width = ROLLING_SAMPLES): SpeedSample[] {
  let total = 0;
  return samples.map((sample, index) => {
    total += sample.speed;
    if (index >= width) total -= samples[index - width]?.speed ?? 0;
    return { at: sample.at, speed: total / Math.min(index + 1, width) };
  });
}

/** One point per run (its mean speed at its midpoint) for the 30-day overview trend. */
export function runAverages(runs: SpeedRun[]): SpeedSample[] {
  return runs.map((run) => ({
    at: (run.start + run.end) / 2,
    speed: speedStats(run.samples)?.average ?? 0,
  }));
}

export function attemptForRun(run: SpeedRun, attempts: ICloudAttempt[], now: number) {
  return attempts.find(
    (attempt) => attempt.startedAt <= run.end && run.start <= (attempt.endedAt ?? now),
  );
}

export function formatSpeed(bytesPerSecond: number): string {
  return `${formatBytes(bytesPerSecond)}/s`;
}

export function formatMegabits(bytesPerSecond: number): string {
  return `${((bytesPerSecond * 8) / 1_000_000).toFixed(1)} Mbit/s`;
}
