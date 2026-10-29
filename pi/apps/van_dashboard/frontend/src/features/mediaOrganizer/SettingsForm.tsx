import { useState } from 'react';
import { classifiers, type Settings } from './schema';
const switches = [
  ['auto_apply', 'Auto apply after downloads'],
  ['auto_apply_new_shows', 'Auto apply new shows'],
  ['mirror_rename', 'Rename mirror copies'],
  ['run_alias_after_apply', 'Rebuild links after apply'],
] as const;
export function SettingsForm({
  settings,
  disabled,
  save,
}: {
  settings: Settings;
  disabled: boolean;
  save: (changes: Partial<Settings>) => Promise<boolean>;
}) {
  const [confidence, setConfidence] = useState<string | null>(null);
  const value = confidence ?? String(settings.auto_apply_min_confidence);
  const valid =
    value.trim() !== '' &&
    Number.isFinite(Number(value)) &&
    Number(value) >= 0 &&
    Number(value) <= 1;
  async function commitConfidence() {
    if (confidence === null || !valid || disabled) return;
    if (
      Number(confidence) === settings.auto_apply_min_confidence ||
      (await save({ auto_apply_min_confidence: Number(confidence) }))
    )
      setConfidence(null);
  }
  return (
    <fieldset disabled={disabled} className="organizer-settings">
      <legend>Settings</legend>
      {switches.map(([key, label]) => (
        <label key={key}>
          <span>{label}</span>
          <input
            role="switch"
            type="checkbox"
            checked={settings[key]}
            onChange={(event) => void save({ [key]: event.target.checked })}
          />
        </label>
      ))}
      <label>
        <span>Minimum auto confidence</span>
        <input
          aria-label="Minimum auto confidence"
          type="number"
          min="0"
          max="1"
          step="0.01"
          value={value}
          aria-invalid={!valid}
          onChange={(event) => setConfidence(event.target.value)}
          onBlur={() => void commitConfidence()}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault();
              event.currentTarget.blur();
            }
          }}
        />
      </label>
      {!valid && <p className="error-message">Enter a confidence between 0 and 1.</p>}
      <label>
        <span>Classifier</span>
        <select
          value={settings.classifier}
          onChange={(event) =>
            void save({ classifier: event.target.value as Settings['classifier'] })
          }
        >
          {classifiers.map((value) => (
            <option key={value}>{value}</option>
          ))}
        </select>
      </label>
    </fieldset>
  );
}
