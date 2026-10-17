import { useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
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
  const id = useId();
  const anchor = useRef<HTMLButtonElement>(null);
  const bubble = useRef<HTMLSpanElement>(null);
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ left: 8, top: 8 });
  useLayoutEffect(() => {
    if (!open) return;
    function place() {
      if (!anchor.current || !bubble.current) return;
      const rect = anchor.current.getBoundingClientRect();
      const box = bubble.current.getBoundingClientRect();
      setPosition({
        left: Math.max(8, Math.min(rect.right - box.width, window.innerWidth - box.width - 8)),
        top:
          rect.bottom + box.height + 16 < window.innerHeight
            ? rect.bottom + 8
            : Math.max(8, rect.top - box.height - 8),
      });
    }
    place();
    window.addEventListener('scroll', place, true);
    window.addEventListener('resize', place);
    return () => {
      window.removeEventListener('scroll', place, true);
      window.removeEventListener('resize', place);
    };
  }, [open]);
  return (
    <span
      className="backup-setting-help"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        ref={anchor}
        type="button"
        aria-label={`About ${label}`}
        aria-describedby={open ? id : undefined}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={() => setOpen(true)}
        onKeyDown={(event) => {
          if (event.key === 'Escape' && open) {
            event.preventDefault();
            event.stopPropagation();
            setOpen(false);
          }
        }}
      >
        {icon}
      </button>
      {open &&
        createPortal(
          <span
            ref={bubble}
            id={id}
            role="tooltip"
            className="backup-setting-tooltip"
            style={position}
          >
            {children}
          </span>,
          anchor.current?.closest('dialog') ?? document.body,
        )}
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
