import { useState } from 'react';

import { BottomSheet } from '../../components/BottomSheet';
import { StatusPill } from '../../components/StatusPill';
import { formatRelativeTime } from '../../utils/format';
import { formatIgnitionDuration } from './countdown';
import { ignitionPauseConfirmation, IgnitionDurationEditor } from './IgnitionDurationEditor';
import { describeIgnitionMonitor } from './presentation';
import type { IgnitionMonitorActions, IgnitionMonitorStatus } from './types';
import './ignition.css';

export interface IgnitionSheetProps {
  open: boolean;
  onClose: () => void;
  status: IgnitionMonitorStatus | null;
  error: Error | null;
  refreshing: boolean;
  remainingSeconds: number | null;
  onRefresh: () => Promise<IgnitionMonitorStatus | null>;
  actions: IgnitionMonitorActions;
}

function deadlineLabel(deadline: number | null): string {
  if (deadline === null) return 'Not paused';
  return new Date(deadline * 1_000).toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
  });
}

export function IgnitionSheet({
  open,
  onClose,
  status,
  error,
  refreshing,
  remainingSeconds,
  onRefresh,
  actions,
}: IgnitionSheetProps) {
  const [durationMinutes, setDurationMinutes] = useState(120);
  const remainingLabel = formatIgnitionDuration(remainingSeconds);
  const presentation = status ? describeIgnitionMonitor(status, remainingLabel) : null;

  const pause = async () => {
    if (!window.confirm(ignitionPauseConfirmation(durationMinutes))) return;
    try {
      await actions.pause(durationMinutes);
    } catch {
      // The action hook reports and reconciles the error.
    }
  };

  const resume = async () => {
    try {
      await actions.resume();
    } catch {
      // The action hook reports and reconciles the error.
    }
  };

  return (
    <BottomSheet
      open={open}
      title="Ignition Monitor"
      description="Service health and the durable pause that suppresses ignition-on actions."
      onClose={onClose}
    >
      <div className="ignition-sheet__toolbar">
        <aside className="ignition-live-panel">
          <strong>Live ignition controls</strong>
          <span>The service stays running while ignition actions are paused.</span>
        </aside>
        <div className="ignition-sheet__actions">
          <button
            type="button"
            className="secondary-button"
            disabled={refreshing || actions.running || !open}
            onClick={() => void onRefresh()}
          >
            {refreshing ? 'Refreshing…' : 'Refresh status'}
          </button>
        </div>
      </div>

      {error && <p className="error-message">{error.message}</p>}
      {!status || !presentation ? (
        <p className="ignition-sheet__empty">
          {error ? 'Ignition monitor state is unavailable.' : 'Checking ignition monitoring…'}
        </p>
      ) : (
        <div className="section-stack">
          <section className="ignition-overview panel-card">
            <div>
              <span>Ignition handling</span>
              <strong>{presentation.summary}</strong>
            </div>
            <StatusPill tone={presentation.tone}>{presentation.label}</StatusPill>
          </section>

          <section className="panel-card ignition-service-card">
            <header>
              <div>
                <h3>ignitionmon.service</h3>
                <p>Systemd service responsible for observing ignition changes.</p>
              </div>
              <StatusPill tone={status.service.running ? 'good' : 'bad'}>
                {status.service.running ? 'Running' : 'Down'}
              </StatusPill>
            </header>
            <dl className="ignition-detail-list">
              <div>
                <dt>Active state</dt>
                <dd>{status.service.activeState}</dd>
              </div>
              <div>
                <dt>Sub-state</dt>
                <dd>{status.service.subState}</dd>
              </div>
              <div>
                <dt>Starts at boot</dt>
                <dd>{status.service.enabled ? 'Yes' : 'No'}</dd>
              </div>
              <div>
                <dt>Unit file</dt>
                <dd>{status.service.unitFileState}</dd>
              </div>
            </dl>
          </section>

          <section className="panel-card ignition-pause-card">
            <header>
              <div>
                <h3>Ignition action pause</h3>
                <p>The monitor service continues running while its actions are paused.</p>
              </div>
              <StatusPill tone={status.monitor.active ? 'good' : 'warning'}>
                {status.monitor.active ? 'Active' : 'Paused'}
              </StatusPill>
            </header>
            <dl className="ignition-detail-list">
              <div>
                <dt>Action state</dt>
                <dd>{status.monitor.active ? 'Enabled' : 'Suppressed'}</dd>
              </div>
              <div>
                <dt>Resumes</dt>
                <dd>{deadlineLabel(status.monitor.deadline)}</dd>
              </div>
              <div>
                <dt>Time remaining</dt>
                <dd>{remainingLabel}</dd>
              </div>
              <div>
                <dt>Status checked</dt>
                <dd>{formatRelativeTime(status.monitor.checkedAt)}</dd>
              </div>
            </dl>
            <IgnitionDurationEditor
              minutes={durationMinutes}
              disabled={actions.running}
              onChange={setDurationMinutes}
            />
            <div className="ignition-control-actions">
              <button
                type="button"
                className="danger-button"
                disabled={actions.running}
                onClick={() => void pause()}
              >
                {actions.running
                  ? 'Applying…'
                  : status.monitor.active
                    ? 'Pause monitoring'
                    : 'Replace pause deadline'}
              </button>
              <button
                type="button"
                className="primary-button"
                disabled={actions.running || status.monitor.active}
                onClick={() => void resume()}
              >
                Resume now
              </button>
            </div>
          </section>
        </div>
      )}
    </BottomSheet>
  );
}
