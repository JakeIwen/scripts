export const IGNITION_MONITOR_MAX_MINUTES = 366 * 24 * 60;

export interface IgnitionServiceStatus {
  activeState: string;
  subState: string;
  unitFileState: string;
  running: boolean;
  enabled: boolean;
}

export type IgnitionMonitorState = 'active' | 'disabled';

export interface IgnitionMonitorOverride {
  version: 1;
  status: IgnitionMonitorState;
  active: boolean;
  deadline: number | null;
  remainingSeconds: number;
  checkedAt: number;
}

export interface IgnitionMonitorStatus {
  service: IgnitionServiceStatus;
  monitor: IgnitionMonitorOverride;
}

export interface IgnitionMonitorMutationResult {
  message: string;
  status: IgnitionMonitorStatus;
}

export interface IgnitionMonitorActions {
  running: boolean;
  pause: (minutes: number) => Promise<boolean>;
  resume: () => Promise<boolean>;
}

export type IgnitionDurationUnit = 'minutes' | 'hours' | 'days';
