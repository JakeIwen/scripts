import { useState } from 'react';
import { BottomSheet } from '../../components/BottomSheet';
import { usePollingResource } from '../../hooks/usePollingResource';
import { formatRelativeTime } from '../../utils/format';
import {
  fetchBackupPriority,
  selectBackupPriority,
  requestMacCapture,
  type BackupPriorityMode,
  type BackupPriorityStatus,
} from './priority';
import type { BackupStatus } from './types';
import './priority.css';

export function BackupPriorityMenu({
  backups,
  refresh,
}: {
  backups: BackupStatus | null;
  refresh: () => Promise<unknown>;
}) {
  const [open, setOpen] = useState(false);
  const resource = usePollingResource({
    load: fetchBackupPriority,
    intervalMs: 5000,
    enabled: open,
  });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function run(action: () => Promise<unknown>, success: string) {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await action();
      await Promise.all([resource.refresh(), refresh()]);
      setMessage(success);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Backup priority request failed.');
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <button
        type="button"
        className="icon-button"
        aria-label="Backup priority"
        title="Backup priority"
        onClick={() => setOpen(true)}
      >
        ⚙
      </button>
      {open && (
        <BottomSheet open title="Backup priority" onClose={() => setOpen(false)}>
          {resource.error && <p className="error-message">{resource.error.message}</p>}
          {error && (
            <p role="alert" className="error-message">
              {error}
            </p>
          )}
          {message && <p role="status">{message}</p>}
          {resource.data ? (
            <>
              <PrioritySelection status={resource.data} busy={busy} run={run} />
              <DeferredBackups status={resource.data} backups={backups} />
              <CapturePriority status={resource.data} busy={busy} run={run} />
            </>
          ) : (
            <p>Loading backup priority…</p>
          )}
        </BottomSheet>
      )}
    </>
  );
}

type ActionRunner = (action: () => Promise<unknown>, success: string) => Promise<void>;
interface PriorityProps {
  status: BackupPriorityStatus;
  busy: boolean;
  run: ActionRunner;
}

function PrioritySelection({ status, busy, run }: PriorityProps) {
  const [draft, setDraft] = useState<BackupPriorityMode | null>(null);
  const selected = draft ?? status.mode;
  const active = status.jobs.find((job) => job.kind === status.mode);
  return (
    <section className="backup-priority-section">
      <h3>Which backup goes first?</h3>
      <p>
        {active
          ? `${active.label} has priority until this recovery point is verified.`
          : 'Normal schedule: daily Pi Borg backups and EXFAT512 snapshots get their reserved window.'}
      </p>
      {status.completed && (
        <p>The prioritized recovery point was verified. Normal priority is restored.</p>
      )}
      <label className="backup-priority-selection">
        Priority
        <select
          value={selected}
          disabled={busy}
          onChange={(event) => setDraft(event.target.value as BackupPriorityMode)}
        >
          <option value="normal">Normal schedule</option>
          {status.jobs.map((job) => (
            <option
              key={job.kind}
              value={job.kind}
              disabled={!job.eligible && job.kind !== status.mode}
            >
              {job.label} until verified{!job.eligible ? ' · already current or not ready' : ''}
            </option>
          ))}
        </select>
      </label>
      <button
        className="primary-button"
        type="button"
        disabled={busy || selected === status.mode}
        onClick={() =>
          void run(
            async () => {
              await selectBackupPriority(selected);
              setDraft(null);
            },
            selected === 'normal'
              ? 'Normal backup priority restored.'
              : 'Priority applied; the selected cloud backup is queued to resume.',
          )
        }
      >
        Apply priority
      </button>
      <p>
        Other backup jobs wait. An existing disk backup finishes safely. Normal priority returns
        after verification; choose Normal schedule to cancel early.
      </p>
      <p>
        Manual pauses on other jobs stay as set. Disk, ignition and Starlink restrictions still
        apply.
      </p>
      {status.jobs
        .filter((job) => !job.eligible && job.reason)
        .map((job) => (
          <small key={job.kind}>
            {job.label}: {job.reason}
          </small>
        ))}
    </section>
  );
}

function DeferredBackups({
  status,
  backups,
}: {
  status: BackupPriorityStatus;
  backups: BackupStatus | null;
}) {
  const rows = [
    { label: 'Pi Borg', time: backups?.borg.lastSuccessAt, running: backups?.borg.running },
    {
      label: 'EXFAT512 snapshot',
      time: backups?.exfatSnapshot.lastSuccessAt,
      running: backups?.exfatSnapshot.running,
    },
  ];
  return (
    <section className="backup-priority-section">
      <h3>Disk backup freshness</h3>
      <dl className="backup-icloud__schedule">
        {rows.map((row) => (
          <div key={row.label}>
            <dt>{row.label}</dt>
            <dd>
              {formatRelativeTime(row.time)} ·{' '}
              {row.running
                ? 'Running'
                : status.mode === 'normal'
                  ? 'Normal schedule'
                  : 'Next run waits for cloud priority'}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function CapturePriority({ status, busy, run }: PriorityProps) {
  const capture = status.capture;
  const queued = capture.phase === 'pending' || capture.phase === 'claimed';
  const eligible =
    status.mode !== 'pi' && status.jobs.some((job) => job.kind === 'time-machine' && job.eligible);
  return (
    <section className="backup-priority-section">
      <h3>Mac Time Machine capture</h3>
      <p>
        {capture.captureComplete
          ? 'The frozen copy is ready. Hourly Mac backups can run alongside the cloud upload.'
          : 'Allow the Mac to gracefully stop its current Time Machine backup so the next frozen capture can begin. Automatic Time Machine scheduling stays enabled.'}
      </p>
      {!capture.coordinatorReady && (
        <p>
          The Mac coordinator needs its one-time update or a fresh connection before
          dashboard-triggered stopping is available.
        </p>
      )}
      {queued && (
        <p role="status">
          {capture.phase === 'pending'
            ? 'Waiting for the Mac and a live capture request.'
            : 'Request delivered to the Mac; waiting for clean capture.'}{' '}
          Permission expires{' '}
          {capture.expiresAt === null
            ? 'soon'
            : new Date(capture.expiresAt * 1000).toLocaleTimeString()}
          .
        </p>
      )}
      {capture.phase === 'expired' && (
        <p>The previous stop permission expired. A new request is required.</p>
      )}
      <button
        className="primary-button"
        type="button"
        disabled={
          busy || !eligible || !capture.coordinatorReady || capture.captureComplete || queued
        }
        onClick={() =>
          void run(
            () => requestMacCapture(),
            'One-time Mac stop permission queued for the next capture.',
          )
        }
      >
        Stop current Mac backup for capture
      </button>
      {capture.phase === 'pending' && (
        <button
          className="secondary-button"
          type="button"
          disabled={busy}
          onClick={() =>
            void run(() => requestMacCapture(true), 'Pending Mac stop permission cancelled.')
          }
        >
          Cancel stop request
        </button>
      )}
    </section>
  );
}
