import { useId, useState, type ReactNode } from 'react';
import { MediaHelp } from '../../components/media/MediaHelp';
import { classifiers, Classifier, type Settings } from './schema';
import { classifierLabels, organizerCopy, settingFields } from './content';

const switches = [
  'auto_apply',
  'auto_apply_new_shows',
  'mirror_rename',
  'run_alias_after_apply',
] as const;
type Props = {
  settings: Settings;
  disabled: boolean;
  saving?: boolean;
  save: (changes: Partial<Settings>) => Promise<boolean>;
};

export function SettingsForm({ settings, disabled, saving, save }: Props) {
  const id = useId();
  return (
    <section className="organizer-settings" aria-label="Organizer settings">
      <div className="media-section-heading">
        <h3>Settings</h3>
        <MediaHelp label="Saving settings">{organizerCopy.settingsHelp}</MediaHelp>
        {disabled && (
          <span role="status">
            {saving ? 'Saving setting…' : 'Settings need an idle organizer and current status.'}
          </span>
        )}
      </div>
      {switches.map((key) => (
        <SettingField key={key} name={key} id={`${id}-${key}`}>
          <input
            id={`${id}-${key}`}
            role="switch"
            type="checkbox"
            checked={settings[key]}
            disabled={disabled}
            onChange={(event) => void save({ [key]: event.target.checked })}
          />
        </SettingField>
      ))}
      <SavedInput
        name="auto_apply_min_confidence"
        value={String(settings.auto_apply_min_confidence)}
        disabled={disabled}
        save={(value) => save({ auto_apply_min_confidence: Number(value) })}
      />
      <SettingField name="classifier" id={`${id}-classifier`}>
        <select
          id={`${id}-classifier`}
          value={settings.classifier}
          disabled={disabled}
          onChange={(event) =>
            void save({ classifier: event.target.value as Settings['classifier'] })
          }
        >
          {classifiers.map((value) => (
            <option key={value} value={value}>
              {classifierLabels[value]}
            </option>
          ))}
        </select>
      </SettingField>
      <SavedInput
        name="codex_model"
        value={settings.codex_model}
        disabled={disabled || settings.classifier !== Classifier.Codex}
        save={(value) => save({ codex_model: value.trim() })}
      />
    </section>
  );
}
function SettingField({
  name,
  id,
  children,
}: {
  name: keyof Settings;
  id: string;
  children: ReactNode;
}) {
  const field = settingFields[name];
  return (
    <div className="organizer-setting">
      <div className="organizer-setting__label">
        <label htmlFor={id}>{field.label}</label>
        <MediaHelp label={field.label}>{field.help}</MediaHelp>
      </div>
      {children}
    </div>
  );
}
function SavedInput({
  name,
  value,
  disabled,
  save,
}: {
  name: 'auto_apply_min_confidence' | 'codex_model';
  value: string;
  disabled: boolean;
  save: (value: string) => Promise<boolean>;
}) {
  const id = useId();
  const [draft, setDraft] = useState<string | null>(null);
  const shown = draft ?? value;
  const numeric = name === 'auto_apply_min_confidence';
  const valid =
    !numeric ||
    (shown.trim() !== '' &&
      Number.isFinite(Number(shown)) &&
      Number(shown) >= 0 &&
      Number(shown) <= 1);
  async function commit() {
    if (draft === null || !valid || disabled) return;
    if (draft === value || (await save(draft))) setDraft(null);
  }
  return (
    <>
      <SettingField name={name} id={id}>
        <input
          id={id}
          type={numeric ? 'number' : 'text'}
          min={numeric ? 0 : undefined}
          max={numeric ? 1 : undefined}
          step={numeric ? 0.01 : undefined}
          placeholder={numeric ? undefined : 'Codex default'}
          value={shown}
          disabled={disabled}
          aria-invalid={!valid}
          aria-describedby={!valid ? `${id}-error` : undefined}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={() => void commit()}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault();
              event.currentTarget.blur();
            }
          }}
        />
      </SettingField>
      {!valid && (
        <p id={`${id}-error`} role="alert" className="error-message">
          Enter a confidence between 0 and 1.
        </p>
      )}
    </>
  );
}
