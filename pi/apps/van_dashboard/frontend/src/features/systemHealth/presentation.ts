import type { StatusTone } from '../../components/StatusPill';
import type { SystemHealthRange, SystemHealthReport } from './types';

export function systemHealthRangeLabel(range: SystemHealthRange): string {
  if (range === 168) return '7 days';
  if (range === 720) return '30 days';
  return `${range} hours`;
}

export function systemHealthTone(
  report: SystemHealthReport | null,
  error: Error | null,
): StatusTone {
  if (error && !report) return 'bad';
  if (!report) return 'neutral';
  if (report.stale || error) return 'warning';
  if (report.level === 'critical') return 'bad';
  if (report.level === 'warning') return 'warning';
  if (report.level === 'good') return 'good';
  return 'neutral';
}

export function systemHealthStatusLabel(
  report: SystemHealthReport | null,
  error: Error | null,
  refreshing: boolean,
): string {
  if (error && !report) return 'No data';
  if (!report) return refreshing ? 'Checking' : 'No data';
  if (report.stale) return 'Stale';
  return report.level;
}
