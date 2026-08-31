import { useState } from 'react';

import type { PollingState } from '../../hooks/usePollingResource';
import { useSingleFlightAction } from '../../hooks/useSingleFlightAction';
import { useToast } from '../../components/ToastProvider';
import type { StatusTone } from '../../components/StatusPill';
import type { CopAlertExecution, DashboardStatus } from './dashboardStatus';
import { setCopAlert } from './api';
import './status.css';

interface ExecutionPresentation {
  label: string;
  detail: string | null;
  tone: StatusTone;
}

export interface DashboardStatusTileProps {
  resource: PollingState<DashboardStatus>;
}

export const COP_CONVERGENCE_INTERVAL_MS = 500;
export const COP_CONVERGENCE_ATTEMPTS = 20;

export function describeCopExecution(execution: CopAlertExecution): ExecutionPresentation {
  if (!execution.available) {
    return {
      label: 'Unavailable',
      detail: execution.error,
      tone: 'bad',
    };
  }

  const detail =
    execution.state === 'blocked'
      ? (execution.lastBlockedDetail ?? execution.lastBlockedReason ?? execution.lastDetail)
      : execution.lastDetail;

  switch (execution.state) {
    case 'idle':
      return { label: 'Idle', detail, tone: 'neutral' };
    case 'starting':
    case 'arming_delay':
      return { label: 'Arming', detail, tone: 'warning' };
    case 'waking':
      return { label: 'Waking', detail, tone: 'warning' };
    case 'active_waiting':
      return { label: 'Active', detail, tone: 'good' };
    case 'blocked':
      return { label: 'Blocked', detail, tone: 'bad' };
    case 'paused_ignition':
      return { label: 'Paused for ignition', detail, tone: 'warning' };
    case 'stopping':
      return { label: 'Stopping', detail, tone: 'neutral' };
    case 'stopped':
      return { label: 'Stopped', detail, tone: 'neutral' };
    default:
      return { label: execution.state ?? 'No data', detail, tone: 'neutral' };
  }
}

function EmptyStatusTile({ resource }: DashboardStatusTileProps) {
  return (
    <button
      type="button"
      className="tile status-tile"
      aria-label="COP ALERT unavailable"
      aria-disabled="true"
      aria-busy={resource.error === null}
    >
      <span className="status-tile__pill">OFF</span>
      <span className="status-tile__heading">
        <span className="status-tile__icon" aria-hidden="true">
          🚨
        </span>
        <span className="status-tile__title" role="heading" aria-level={2}>
          COP ALERT
        </span>
      </span>
      <span className="status-tile__summary">
        {resource.error?.message ?? 'Reading COP request and execution state…'}
      </span>
      <span className="status-tile__status-lines">
        <span className="status-tile__status-line">
          <span>CAN wake</span>
          <span className="status-tile__wake-pill status-tile__wake-pill--offline">OFFLINE</span>
        </span>
        <span className="status-tile__status-line">
          <span>ext_led</span>
          <span>No data</span>
        </span>
      </span>
    </button>
  );
}

export function copConverged(status: DashboardStatus, requested: boolean): boolean {
  if (status.copAlert.requested !== requested) return false;
  const state = status.copExecution.state;
  if (!requested) return state === 'idle' || state === 'stopped';
  return (
    !status.copExecution.available ||
    state === 'active_waiting' ||
    state === 'blocked' ||
    state === 'paused_ignition' ||
    state === 'stopping' ||
    state === 'stopped'
  );
}

type RefreshStatus = PollingState<DashboardStatus>['refresh'];
type Wait = (milliseconds: number) => Promise<void>;
type CopMutation = (requested: boolean) => ReturnType<typeof setCopAlert>;
type SetRequestedOverride = (requested: boolean | null) => void;
type ShowToast = (message: string, tone?: 'normal' | 'error') => void;

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

export async function convergeCopStatus(
  refresh: RefreshStatus,
  requested: boolean,
  pause: Wait = wait,
): Promise<DashboardStatus | null> {
  let latest: DashboardStatus | null = null;
  for (let attempt = 0; attempt < COP_CONVERGENCE_ATTEMPTS; attempt += 1) {
    await pause(COP_CONVERGENCE_INTERVAL_MS);
    latest = await refresh();
    if (latest && copConverged(latest, requested)) return latest;
  }
  return latest;
}

export async function executeCopAlertChange(
  requested: boolean,
  mutate: CopMutation,
  refresh: RefreshStatus,
  setRequestedOverride: SetRequestedOverride,
  showToast: ShowToast,
  pause: Wait = wait,
): Promise<boolean> {
  try {
    const result = await mutate(requested);
    setRequestedOverride(result.request.requested);
    showToast(result.message);
    await convergeCopStatus(refresh, requested, pause);
    return true;
  } catch (error) {
    showToast(error instanceof Error ? error.message : String(error), 'error');
    return false;
  } finally {
    await refresh();
    setRequestedOverride(null);
  }
}

interface CopTilePresentation {
  label: string;
  state: 'idle' | 'offline' | 'arming' | 'waking' | 'active' | 'blocked' | 'paused';
  detail: string;
  wakeDetail: string | null;
}

function lastBlockedDetail(execution: CopAlertExecution): string | null {
  const details = [execution.lastBlockedReason, execution.lastBlockedDetail].filter(
    (detail, index, values): detail is string =>
      Boolean(detail) && values.indexOf(detail) === index,
  );
  return details.length > 0 ? details.join(' · ') : null;
}

function copTilePresentation(
  requested: boolean,
  execution: CopAlertExecution,
): CopTilePresentation {
  const state = execution.state;
  if (!requested) {
    const idle = execution.available && state === 'idle';
    return {
      label: idle ? 'IDLE' : 'OFFLINE',
      state: idle ? 'idle' : 'offline',
      detail: 'Tap to wake the dashcam and arm the exterior alert',
      wakeDetail: lastBlockedDetail(execution),
    };
  }
  if (!execution.available || state === 'stopped' || state === 'stopping') {
    return {
      label: 'OFFLINE',
      state: 'offline',
      detail: 'Exterior alert armed · CAN wake supervisor unavailable',
      wakeDetail: null,
    };
  }
  if (state === 'idle' || state === 'starting' || state === 'arming_delay') {
    return {
      label: 'ARMING',
      state: 'arming',
      detail: 'Arming dashcam wake…',
      wakeDetail: null,
    };
  }
  if (state === 'waking') {
    return {
      label: 'WAKING',
      state: 'waking',
      detail: 'Waking dashcam/accessory network…',
      wakeDetail: null,
    };
  }
  if (state === 'active_waiting') {
    return {
      label: 'ACTIVE',
      state: 'active',
      detail: 'Exterior alert armed · CAN wake waiting',
      wakeDetail: null,
    };
  }
  if (state === 'blocked') {
    return {
      label: 'BLOCKED',
      state: 'blocked',
      detail: 'Exterior alert armed · CAN wake waiting',
      wakeDetail: execution.lastDetail ?? execution.lastBlockedDetail ?? null,
    };
  }
  if (state === 'paused_ignition') {
    return {
      label: 'PAUSED',
      state: 'paused',
      detail: 'Exterior alert paused while ignition is on',
      wakeDetail: null,
    };
  }
  return {
    label: 'OFFLINE',
    state: 'offline',
    detail: 'Exterior alert armed · CAN wake supervisor unavailable',
    wakeDetail: null,
  };
}

export function DashboardStatusTile({ resource }: DashboardStatusTileProps) {
  const [requestedOverride, setRequestedOverride] = useState<boolean | null>(null);
  const action = useSingleFlightAction();
  const { showToast } = useToast();
  const authoritativeStatus = resource.data;
  const status =
    authoritativeStatus && requestedOverride !== null
      ? {
          ...authoritativeStatus,
          copAlert: { ...authoritativeStatus.copAlert, requested: requestedOverride },
          copExecution:
            requestedOverride && authoritativeStatus.copExecution.state === 'idle'
              ? { ...authoritativeStatus.copExecution, state: 'arming_delay' as const }
              : authoritativeStatus.copExecution,
        }
      : authoritativeStatus;
  if (!status) return <EmptyStatusTile resource={resource} />;

  const requested = status.copAlert.requested;
  const presentation = copTilePresentation(requested, status.copExecution);

  const toggle = async () => {
    const requested = !status.copAlert.requested;
    await action.run(() =>
      executeCopAlertChange(
        requested,
        setCopAlert,
        resource.refresh,
        setRequestedOverride,
        showToast,
      ),
    );
  };

  return (
    <button
      type="button"
      className={`tile status-tile ${requested ? 'status-tile--active' : ''}`.trim()}
      onClick={() => void toggle()}
      aria-label={requested ? 'Disarm COP ALERT' : 'Arm COP ALERT'}
      aria-pressed={requested}
      aria-busy={action.running}
      disabled={action.running}
    >
      <span className="status-tile__pill">{requested ? 'ACTIVE' : 'OFF'}</span>
      {requested && (
        <span className="visually-hidden" aria-hidden="true">
          Requested
        </span>
      )}
      <span className="visually-hidden" aria-hidden="true">
        {describeCopExecution(status.copExecution).label}
      </span>
      {status.copExecution.lastBlockedDetail && (
        <span className="visually-hidden" aria-hidden="true">
          {status.copExecution.lastBlockedDetail}
        </span>
      )}
      <span className="status-tile__heading">
        <span className="status-tile__icon" aria-hidden="true">
          🚨
        </span>
        <span className="status-tile__title" role="heading" aria-level={2}>
          COP ALERT
        </span>
      </span>
      <span className="status-tile__summary">{presentation.detail}</span>
      <span className="status-tile__status-lines">
        <span className="status-tile__status-line">
          <span>CAN wake</span>
          <span className={`status-tile__wake-pill status-tile__wake-pill--${presentation.state}`}>
            {presentation.label}
          </span>
        </span>
        <span className="status-tile__status-line">
          <span>ext_led</span>
          <span title={status.copLed.lastError ?? undefined}>{status.copLed.message}</span>
        </span>
        {presentation.wakeDetail && (
          <span className="status-tile__status-line status-tile__status-line--detail">
            <span>{requested ? 'Wake detail' : 'Last block'}</span>
            <span>{presentation.wakeDetail}</span>
          </span>
        )}
      </span>
    </button>
  );
}
