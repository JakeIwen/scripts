import { ICloudPauseSelector } from './ICloudPauseSelector';
import { useState } from 'react';
import { useSingleFlightAction } from '../../hooks/useSingleFlightAction';
import { controlICloudBackup } from './api';
import { iCloudResumePresentation } from './cloudPresentation';
import {
  INDEFINITE_PAUSE,
  type AvailableICloudStatus,
  type ICloudBackupKind,
  type ICloudControlAction,
  type ICloudStatus,
} from './icloud';

interface ICloudBackupControlsProps {
  kind: ICloudBackupKind;
  status: AvailableICloudStatus;
  other?: ICloudStatus | null;
  refresh: () => Promise<unknown>;
  blocked: boolean;
}

export function ICloudBackupControls({
  kind,
  status,
  other,
  refresh,
  blocked,
}: ICloudBackupControlsProps) {
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { running, run } = useSingleFlightAction();
  const disabled = blocked || running;
  const manuallyPaused = status.manualPauseUntil != null || status.manualPauseIndefinite;
  const resume = iCloudResumePresentation(status);
  async function perform(action: ICloudControlAction, minutes = INDEFINITE_PAUSE) {
    await run(async () => {
      setError(null);
      setMessage(null);
      try {
        setMessage(await controlICloudBackup(action, minutes, kind));
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        await refresh();
      }
    });
  }
  return (
    <div className="backup-cloud-controls">
      {status.manualPauseIndefinite && <p>Paused indefinitely. Resume manually when ready.</p>}
      {status.manualPauseUntil != null && (
        <p>
          Automatic resume:{' '}
          <time dateTime={new Date(status.manualPauseUntil * 1000).toISOString()}>
            {new Date(status.manualPauseUntil * 1000).toLocaleString()}
          </time>
          . Checks run every minute.
        </p>
      )}
      <div className="backup-cloud-controls__actions">
        <button
          type="button"
          className="primary-button"
          disabled={disabled || (status.running && !manuallyPaused)}
          title={resume.help}
          aria-description={resume.help}
          onClick={() => void perform('resume')}
        >
          {running ? 'Applying…' : resume.label}
        </button>
        <ICloudPauseSelector
          onPause={(minutes) => void perform('pause', minutes)}
          disabled={disabled}
        />
        {other?.available && !status.running && (other.running || other.resumePending) && (
          <button
            type="button"
            className="secondary-button"
            disabled={disabled}
            title={`Pause ${kind === 'pi' ? 'Mac' : 'Pi'} iCloud indefinitely, then resume this backup. Existing safety checks still apply.`}
            onClick={() => void perform('take-turn')}
          >
            Prioritize &amp; run
          </button>
        )}
      </div>
      {message && <p role="status">{message}</p>}
      {error && (
        <p role="alert" className="error-message">
          {error}
        </p>
      )}
    </div>
  );
}
