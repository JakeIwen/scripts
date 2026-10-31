import { useState } from 'react';
import { isGated, MAX_MODE_HOURS, modeEdit, timeLabel } from './presentation';
import type { MeteredStatus, ModeValue } from './schema';
import type { MeteredControls } from './useMetered';

function ModeForm({ controls, status }: { controls: MeteredControls; status: MeteredStatus }) {
  const [mode, setMode] = useState<ModeValue>(status.policy.mode.value);
  const [hours, setHours] = useState('1');
  const [error, setError] = useState<string | null>(null);
  const current = status.policy.mode;
  return (
    <div>
      <p className="metered-note">
        Current Pi mode: {current.value}
        {current.until !== null && ` until ${timeLabel(current.until)}`}
      </p>
      <form
        className="metered-form"
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          try {
            const edit = modeEdit(mode, hours);
            setError(null);
            void controls.changePolicy(edit);
          } catch (reason) {
            setError(reason instanceof Error ? reason.message : String(reason));
          }
        }}
      >
        <label>
          Pi mode
          <select
            value={mode}
            onChange={(event) => setMode(event.currentTarget.value as ModeValue)}
          >
            <option value="auto">Auto</option>
            <option value="metered">Metered</option>
            <option value="unmetered">Unmetered</option>
          </select>
        </label>
        {mode !== 'auto' && (
          <label>
            Duration (hours)
            <input
              type="number"
              min="0"
              max={MAX_MODE_HOURS}
              step="any"
              value={hours}
              required={mode === 'unmetered'}
              onChange={(event) => setHours(event.currentTarget.value)}
            />
          </label>
        )}
        <button className="secondary-button" type="submit" disabled={controls.blocked}>
          Apply mode
        </button>
      </form>
      {error && (
        <p className="error-message" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

export function MeteredAdvanced({
  controls,
  status,
}: {
  controls: MeteredControls;
  status: MeteredStatus;
}) {
  return (
    <details className="metered-advanced">
      <summary>Advanced</summary>
      <ModeForm controls={controls} status={status} />
      <h4>Not gated</h4>
      <div className="metered-exemptions">
        {status.registry
          .filter((row) => row.host === 'pi' && isGated(row))
          .map((row) => (
            <label key={row.id}>
              <input
                type="checkbox"
                checked={status.policy.exempt.includes(row.id)}
                disabled={controls.blocked}
                onChange={(event) =>
                  void controls.changePolicy({
                    op: 'exempt',
                    consumer: row.id,
                    enabled: event.currentTarget.checked,
                  })
                }
              />
              {row.label}
            </label>
          ))}
      </div>
    </details>
  );
}
