import type { StatusTone } from '../../components/StatusPill';
import type { SystemHealthRange, SystemHealthReport } from './types';

export function systemHealthRangeLabel(range: SystemHealthRange): string {
  if (range === 168) return '7 days';
  if (range === 720) return '30 days';
  return `${range} hours`;
}

export function systemHealthTone(
  report: SystemHealthReport | null,
  _error: Error | null,
): StatusTone {
  if (!report) return 'neutral';
  if (report.level === 'critical') return 'bad';
  if (report.level === 'warning') return 'warning';
  if (report.level === 'good') return 'good';
  return 'neutral';
}

export function systemHealthStatusLabel(
  report: SystemHealthReport | null,
  _error: Error | null,
  _refreshing: boolean,
): string {
  if (!report) return 'No data';
  return report.level;
}
