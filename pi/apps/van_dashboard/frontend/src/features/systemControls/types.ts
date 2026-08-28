export type SystemPowerAction = 'reboot' | 'power-down';
export type SystemPowerOperationStatus = 'idle' | 'running' | 'complete' | 'error';

export interface SystemPowerOperation {
  status: SystemPowerOperationStatus;
  action: SystemPowerAction | null;
  startedAt: number | null;
  completedAt: number | null;
  error: string | null;
}

export interface SystemPowerRequestResult {
  message: string;
  operation: SystemPowerOperation;
}

export interface DashboardRestartResult {
  message: string;
  scheduledAt: number;
}

export type SystemControlPhase =
  | 'idle'
  | 'submitting-power'
  | 'watching-power'
  | 'power-complete'
  | 'power-disconnected'
  | 'submitting-restart'
  | 'watching-restart'
  | 'restart-complete'
  | 'error';
