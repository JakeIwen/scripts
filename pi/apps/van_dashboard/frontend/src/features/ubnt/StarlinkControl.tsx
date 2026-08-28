import { useState } from 'react';

import { StatusPill, type StatusTone } from '../../components/StatusPill';
import { useToast } from '../../components/ToastProvider';
import { useSingleFlightAction } from '../../hooks/useSingleFlightAction';
import { toggleStarlinkPower } from '../status/api';
import type { DashboardStatus, StarlinkStatus } from '../status/dashboardStatus';
import './ubnt.css';

export interface StarlinkStatusResource {
  data: DashboardStatus | null;
  error: Error | null;
  refreshing: boolean;
  refresh: () => Promise<DashboardStatus | null>;
}

export interface StarlinkControlProps {
  resource: StarlinkStatusResource;
}

function tone(status: StarlinkStatus | null): StatusTone {
  if (!status || !status.available || status.state === 'unknown') return 'bad';
  return status.state === 'on' ? 'good' : 'neutral';
}

function stateLabel(status: StarlinkStatus | null, running: boolean): string {
  if (running || status?.changing) return 'Changing';
  if (!status || !status.available || status.state === 'unknown') return 'No data';
  return status.state === 'on' ? 'On' : 'Off';
}

export function starlinkConfirmation(status: StarlinkStatus): string {
  const next = status.state === 'on' ? 'off' : 'on';
  return `Turn Starlink power ${next}?\n\nThis changes the van's Starlink power switch. Network routing will reconcile separately.`;
}

export function StarlinkControl({ resource }: StarlinkControlProps) {
  const [returnedStatus, setReturnedStatus] = useState<StarlinkStatus | null>(null);
  const action = useSingleFlightAction();
  const { showToast } = useToast();
  const status = returnedStatus ?? resource.data?.starlink ?? null;
  const known = status?.available === true && (status.state === 'on' || status.state === 'off');
  const disabled = !known || status?.changing === true || action.running || resource.refreshing;

  const toggle = async () => {
    if (!status || !known || disabled) return;
    if (!window.confirm(starlinkConfirmation(status))) return;

    await action.run(async () => {
      try {
        const result = await toggleStarlinkPower();
        setReturnedStatus(result.status);
        showToast(result.message);
      } catch (reason) {
        showToast(reason instanceof Error ? reason.message : String(reason), 'error');
      } finally {
        // The first call drains any status request that overlapped the toggle;
        // the second is guaranteed to begin after the mutation outcome.
        await resource.refresh();
        const refreshed = await resource.refresh();
        if (refreshed) setReturnedStatus(null);
      }
    });
  };

  return (
    <section className="starlink-control panel-card" aria-labelledby="starlink-control-title">
      <div className="starlink-control__identity">
        <span aria-hidden="true">🛰️</span>
        <span>
          <strong id="starlink-control-title">Starlink power</strong>
          <small>
            {status?.lastError ??
              resource.error?.message ??
              (known ? `Tuya switch is ${status.state}` : 'Tuya status unavailable')}
          </small>
        </span>
      </div>
      <StatusPill tone={tone(status)}>{stateLabel(status, action.running)}</StatusPill>
      <button
        type="button"
        className={status?.state === 'on' ? 'danger-button' : 'primary-button'}
        disabled={disabled}
        aria-pressed={known ? status.state === 'on' : undefined}
        onClick={() => void toggle()}
      >
        {action.running || status?.changing
          ? 'Changing power…'
          : status?.state === 'on'
            ? 'Turn Starlink off'
            : 'Turn Starlink on'}
      </button>
    </section>
  );
}
