import type { StatusTone } from '../../components/StatusPill';
import type { IgnitionMonitorStatus } from './types';

export interface IgnitionPresentation {
  label: string;
  summary: string;
  tone: StatusTone;
}

export function describeIgnitionMonitor(
  status: IgnitionMonitorStatus,
  remainingLabel: string,
): IgnitionPresentation {
  if (!status.service.running) {
    return {
      label: 'Service down',
      summary: 'ignitionmon.service is not running',
      tone: 'bad',
    };
  }
  if (!status.monitor.active) {
    return {
      label: 'Paused',
      summary: `Ignition handling resumes in ${remainingLabel}`,
      tone: 'warning',
    };
  }
  if (!status.service.enabled) {
    return {
      label: 'Running',
      summary: 'Watching ignition, but not enabled for the next boot',
      tone: 'warning',
    };
  }
  return {
    label: 'Active',
    summary: 'Watching for ignition changes',
    tone: 'good',
  };
}
