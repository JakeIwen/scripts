import { useState } from 'react';
import { useSingleFlightAction } from '../../hooks/useSingleFlightAction';
import { controlICloudBackup } from './api';
import type { AvailableICloudStatus, ICloudControlAction } from './icloud';

interface ICloudBackupControlsProps {
  status: AvailableICloudStatus;
  refresh: () => Promise<unknown>;
  blocked: boolean;
}

export function ICloudBackupControls({
  status,
  refresh,
  blocked,
}: ICloudBackupControlsProps) {
  const [minutes, setMinutes] = useState('60');
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { running, run } = useSingleFlightAction();
  const disabled = blocked || running;
  async function perform(action: ICloudControlAction) {
    await run(async () => {
      setError(null);
      setMessage(null);
      try {
        setMessage(await controlICloudBackup(action, minutes));
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        await refresh();
      }
    });
  }
  return (
    <div className="backup-cloud-controls">
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
          disabled={disabled || (status.running && status.manualPauseUntil == null)}
          onClick={() => void perform('resume')}
        >
          {running ? 'Applying…' : 'Resume now'}
        </button>
        <label>
          Pause for
          <select
            value={minutes}
            onChange={(event) => setMinutes(event.target.value)}
            disabled={disabled}
          >
            <option value="15">15 minutes</option>
            <option value="30">30 minutes</option>
            <option value="60">1 hour</option>
            <option value="240">4 hours</option>
            <option value="720">12 hours</option>
            <option value="1440">24 hours</option>
            <option value="10080">7 days</option>
          </select>
        </label>
        <button
          type="button"
          className="secondary-button"
          disabled={disabled}
          onClick={() => void perform('pause')}
        >
          Pause
        </button>
      </div>
      <small>
        Resume retries saved work when the backup lock, disk, ignition and network checks allow it.
      </small>
      {message && <p role="status">{message}</p>}
      {error && (
        <p role="alert" className="error-message">
          {error}
        </p>
      )}
    </div>
  );
}
