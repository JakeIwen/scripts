import { useState } from 'react';
import { ssidError } from './presentation';
import type { MeteredControls } from './useMetered';

export function MeteredSSIDs({ ssids, controls }: { ssids: string[]; controls: MeteredControls }) {
  const [ssid, setSSID] = useState('');
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="metered-ssids">
      <h4>Metered SSIDs</h4>
      <div className="metered-chips">
        {ssids.map((name) => (
          <span className="metered-chip" key={name}>
            <code>{name}</code>
            <button
              type="button"
              aria-label={`Remove SSID ${name}`}
              disabled={controls.blocked}
              onClick={() => void controls.changeSSID('remove', name)}
            >
              ×
            </button>
          </span>
        ))}
        {!ssids.length && <span className="metered-note">No metered SSIDs</span>}
      </div>
      <form
        className="metered-form"
        onSubmit={async (event) => {
          event.preventDefault();
          const invalid = ssidError(ssid);
          setError(invalid);
          if (!invalid && (await controls.changeSSID('add', ssid))) setSSID('');
        }}
      >
        <label>
          SSID
          <input
            value={ssid}
            onChange={(event) => setSSID(event.currentTarget.value)}
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            placeholder="Exact, case-sensitive SSID"
          />
        </label>
        <button className="secondary-button" type="submit" disabled={controls.blocked}>
          Add SSID
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
