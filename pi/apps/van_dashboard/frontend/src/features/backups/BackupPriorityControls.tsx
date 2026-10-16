import { useEffect, useState } from 'react';
import {
  fetchBackupPriority,
  requestMacCapture,
  selectBackupPriority,
  type BackupPriorityMode,
  type BackupPriorityStatus,
} from './priority';
import { SettingHelp } from './BackupSettingField';

export function BackupPriorityControls({
  busy,
  setBusy,
  refresh,
}: {
  busy: boolean;
  setBusy: (value: boolean) => void;
  refresh: () => Promise<unknown>;
}) {
  const [status, setStatus] = useState<BackupPriorityStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => {
    const abort = new AbortController();
    fetchBackupPriority(abort.signal)
      .then(setStatus)
      .catch((reason) => {
        if (!abort.signal.aborted) setError(String(reason.message));
      });
    return () => abort.abort();
  }, []);
  async function run(action: () => Promise<unknown>, success: string) {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await action();
      setStatus(await fetchBackupPriority(new AbortController().signal));
      await refresh();
      setMessage(success);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not change priority.');
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="backup-settings-priority">
      <div className="backup-settings-section__title">
        <h3>Cloud priority</h3>
        <SettingHelp label="Cloud priority">
          A selected cloud job goes first until its recovery point is verified. Pi Borg, EXFAT512
          and other disk backups can run alongside it. Other manual pauses remain unchanged.
        </SettingHelp>
      </div>
      {error && (
        <p role="alert" className="error-message">
          {error}
        </p>
      )}
      {message && <p role="status">{message}</p>}
      {!status ? (
        <p role="status">Loading…</p>
      ) : (
        <>
          <PriorityChoices status={status} busy={busy} run={run} />
          <CapturePriority status={status} busy={busy} run={run} />
        </>
      )}
    </div>
  );
}

interface PriorityProps {
  status: BackupPriorityStatus;
  busy: boolean;
  run: (action: () => Promise<unknown>, success: string) => Promise<void>;
}

function PriorityChoices({ status, busy, run }: PriorityProps) {
  const options: { mode: BackupPriorityMode; label: string; icon: string }[] = [
    { mode: 'normal', label: 'Scheduled order', icon: '↔' },
    { mode: 'pi', label: 'Pi first', icon: '◈' },
    { mode: 'time-machine', label: 'Mac first', icon: '⌘' },
  ];
  return (
    <div className="backup-priority-choices" aria-label="Cloud priority">
      {options.map((option) => {
        const job = status.jobs.find((job) => job.kind === option.mode);
        const unavailable = !!job && !job.eligible && status.mode !== option.mode;
        return (
          <button
            key={option.mode}
            type="button"
            className={status.mode === option.mode ? 'selected' : ''}
            aria-pressed={status.mode === option.mode}
            disabled={busy || unavailable}
            title={
              unavailable
                ? (job?.reason ?? 'This backup is already current.')
                : option.mode === 'normal'
                  ? 'Use normal cloud scheduling.'
                  : 'Give this cloud copy priority until verified.'
            }
            onClick={() => void run(() => selectBackupPriority(option.mode), 'Priority saved.')}
          >
            <span aria-hidden="true">{option.icon}</span>
            {option.label}
            <small>
              {option.mode === 'normal'
                ? 'Automatic'
                : unavailable
                  ? 'Already current'
                  : 'Until verified'}
            </small>
          </button>
        );
      })}
    </div>
  );
}

function CapturePriority({ status, busy, run }: PriorityProps) {
  return (
    <section className="backup-settings-section">
      <div className="backup-settings-section__title">
        <h4>Mac capture</h4>
        <SettingHelp label="Mac capture">
          Authorize the installed Mac coordinator to gracefully stop its current Time Machine backup
          for one new frozen capture. Automatic Mac backups remain enabled. Permission expires after
          30 minutes.
        </SettingHelp>
      </div>
      <span className="backup-settings-chip">
        {status.capture.captureComplete
          ? 'Frozen copy ready'
          : !status.capture.coordinatorReady
            ? 'Mac coordinator unavailable'
            : status.capture.phase === 'pending' || status.capture.phase === 'claimed'
              ? 'Stop request queued'
              : 'Ready'}
      </span>
      <div className="backup-settings-capture-actions">
        <button
          type="button"
          className="secondary-button"
          disabled={
            busy ||
            status.mode === 'pi' ||
            !status.jobs.some((job) => job.kind === 'time-machine' && job.eligible) ||
            !status.capture.coordinatorReady ||
            status.capture.captureComplete ||
            ['pending', 'claimed'].includes(status.capture.phase)
          }
          onClick={() => void run(() => requestMacCapture(), 'Mac stop permission queued.')}
        >
          Stop Mac backup for capture
        </button>
        {status.capture.phase === 'pending' && (
          <button
            type="button"
            className="secondary-button"
            disabled={busy}
            onClick={() => void run(() => requestMacCapture(true), 'Stop request cancelled.')}
          >
            Cancel request
          </button>
        )}
      </div>
    </section>
  );
}
