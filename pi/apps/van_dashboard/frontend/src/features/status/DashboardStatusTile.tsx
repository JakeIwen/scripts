import { useState } from 'react';

import type { PollingState } from '../../hooks/usePollingResource';
import { useSingleFlightAction } from '../../hooks/useSingleFlightAction';
import { useToast } from '../../components/ToastProvider';
import { KeyValueList } from '../../components/KeyValueList';
import { StatusPill, type StatusTone } from '../../components/StatusPill';
import { Tile } from '../../components/Tile';
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
  const failed = resource.error !== null;
  return (
    <Tile
      icon="🚨"
      title="COP ALERT"
      summary={failed ? resource.error?.message : 'Reading COP request and execution state…'}
      status={
        <StatusPill tone={failed ? 'bad' : 'neutral'}>{failed ? 'No data' : 'Loading'}</StatusPill>
      }
      tone={failed ? 'bad' : 'neutral'}
      className="status-tile"
    >
      <ReadOnlyNotice />
    </Tile>
  );
}

function ReadOnlyNotice() {
  return (
    <aside className="status-tile__read-only" aria-label="COP control is loading">
      <strong>Control unavailable</strong>
      <span>Wait for authoritative COP and supervisor status.</span>
    </aside>
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

  const execution = describeCopExecution(status.copExecution);
  const requested = status.copAlert.requested;
  const requestLabel = requested ? 'On' : 'Off';
  const summary = requested
    ? `COP ALERT is requested. Execution is ${execution.label.toLowerCase()}.`
    : 'COP ALERT is not requested.';
  const error =
    status.copAlert.lastError ?? status.copLed.lastError ?? resource.error?.message ?? null;

  const items = [
    { label: 'Request', value: requestLabel },
    { label: 'Execution', value: execution.label },
    { label: 'Exterior alert', value: status.copLed.message },
    ...(execution.detail ? [{ label: 'Wake detail', value: execution.detail }] : []),
    ...(error ? [{ label: 'Current error', value: error }] : []),
  ];

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
    <Tile
      icon="🚨"
      title="COP ALERT"
      summary={summary}
      status={
        <StatusPill tone={requested ? 'good' : 'neutral'}>
          {requested ? 'Requested' : 'Off'}
        </StatusPill>
      }
      tone={requested ? execution.tone : 'neutral'}
      className="status-tile"
    >
      <KeyValueList items={items} className="status-tile__details" />
      <button
        type="button"
        className={requested ? 'danger-button' : 'primary-button'}
        disabled={action.running}
        onClick={() => void toggle()}
      >
        {action.running
          ? 'Waiting for supervisor…'
          : requested
            ? 'Disarm COP ALERT'
            : 'Arm COP ALERT'}
      </button>
    </Tile>
  );
}
