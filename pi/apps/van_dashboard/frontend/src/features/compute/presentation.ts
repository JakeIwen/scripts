import type { StatusTone } from '../../components/StatusPill';
import type { ComputeJobState, ComputeRange, ComputeReport } from './types';

export function computeRangeLabel(range: ComputeRange): string {
  if (range === 168) return '7 days';
  if (range === 720) return '30 days';
  return `${range} hours`;
}

export function computeTone(report: ComputeReport | null, error: Error | null): StatusTone {
  if (!report) return 'neutral';
  if (report.status.localRunning > 0) return 'warning';
  if (report.status.available) return error || report.summary.failed > 0 ? 'warning' : 'good';
  return report.status.queued > 0 ? 'warning' : 'neutral';
}

export function computeStatusLabel(report: ComputeReport | null, _refreshing: boolean): string {
  if (!report) return 'No data';
  if (report.status.running > 0) return 'Busy';
  if (report.status.localRunning > 0) return 'Pi fallback';
  return report.status.available ? 'Online' : 'Offline';
}

export function computeJobStateLabel(state: ComputeJobState): string {
  if (state === 'done') return 'Done';
  if (state === 'failed') return 'Failed';
  if (state === 'running') return 'Running';
  return 'Queued';
}

export function formatComputeDuration(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  const seconds = Math.max(0, value);
  if (seconds < 1) {
    const milliseconds = Math.round(seconds * 1_000);
    if (milliseconds < 1_000) return `${milliseconds} ms`;
  }
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 2 : 1)} s`;
  if (seconds < 3_600) return `${(seconds / 60).toFixed(1)} min`;
  return `${(seconds / 3_600).toFixed(1)} h`;
}
