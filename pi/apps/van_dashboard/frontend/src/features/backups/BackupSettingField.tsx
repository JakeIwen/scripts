import { useId } from 'react';
import { TooltipButton } from '../../components/TooltipButton';
import type { BackupSettingField as Field } from './settings';
import type { SettingDraftValue } from './settingsDraft';

export function SettingHelp({
  label,
  children,
  icon = '?',
}: {
  label: string;
  children: string;
  icon?: '?' | 'i';
}) {
  return (
    <span className="backup-setting-help">
      <TooltipButton aria-label={`About ${label}`} tooltip={children}>
        {icon}
      </TooltipButton>
    </span>
  );
}

export function BackupSettingField({
  field,
  value,
  onChange,
  onCommit,
  disabled,
  error,
}: {
  field: Field;
  value: SettingDraftValue;
  onChange: (value: SettingDraftValue) => void;
  onCommit: () => void;
  disabled: boolean;
  error?: string;
}) {
  const id = useId();
  return (
    <div
      className={`backup-setting-field ${field.kind === 'toggle' ? 'backup-setting-field--toggle' : ''}`}
    >
      <div className="backup-setting-field__label">
        <label htmlFor={id}>{field.label}</label>
        <SettingHelp label={field.label} icon={field.group === 'hotspares' ? 'i' : '?'}>
          {field.help}
        </SettingHelp>
      </div>
      {field.kind === 'toggle' ? (
        <button
          id={id}
          type="button"
          role="switch"
          className="backup-setting-switch"
          aria-label={field.label}
          aria-checked={!!value}
          disabled={disabled}
          onClick={() => onChange(!value)}
        >
          <span aria-hidden="true" />
        </button>
      ) : (
        <div className="backup-setting-field__input">
          {field.kind === 'hour' ? (
            <select
              id={id}
              value={Number(value)}
              disabled={disabled}
              aria-invalid={!!error}
              title={error}
              onChange={(event) => onChange(Number(event.target.value))}
            >
              {Array.from(
                { length: field.maximum - field.minimum + 1 },
                (_, index) => index + field.minimum,
              ).map((hour) => (
                <option key={hour} value={hour}>
                  {String(hour).padStart(2, '0')}:00
                </option>
              ))}
            </select>
          ) : (
            <input
              id={id}
              type="number"
              inputMode={field.kind === 'bandwidth' ? 'decimal' : 'numeric'}
              min={field.minimum}
              max={field.maximum}
              step={field.kind === 'bandwidth' ? 'any' : '1'}
              value={typeof value === 'boolean' ? '' : value}
              disabled={disabled}
              required
              onBlur={onCommit}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === 'Escape') {
                  if (event.key === 'Enter') event.preventDefault();
                  event.currentTarget.blur();
                }
              }}
              aria-invalid={!!error}
              title={error}
              onChange={(event) =>
                onChange(event.target.value === '' ? '' : Number(event.target.value))
              }
            />
          )}
          {field.kind !== 'hour' && <span>{field.unit}</span>}
        </div>
      )}
    </div>
  );
}
