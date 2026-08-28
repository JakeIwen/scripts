import { useEffect, useState } from 'react';

import { formatIgnitionDuration } from './countdown';
import { IGNITION_MONITOR_MAX_MINUTES, type IgnitionDurationUnit } from './types';

export const IGNITION_MAX_MINUTES = IGNITION_MONITOR_MAX_MINUTES;
export const IGNITION_DURATION_PRESETS = [15, 30, 60, 120, 480, 1_440, 4_320, 10_080] as const;

const UNIT_SETTINGS: Record<
  IgnitionDurationUnit,
  { factor: number; maximum: number; label: string }
> = {
  minutes: { factor: 1, maximum: 720, label: 'minutes' },
  hours: { factor: 60, maximum: 168, label: 'hours' },
  days: { factor: 1_440, maximum: 366, label: 'days' },
};

interface IgnitionDurationEditorProps {
  minutes: number;
  disabled: boolean;
  onChange: (minutes: number) => void;
}

export function normalizeIgnitionMinutes(value: number): number {
  if (!Number.isFinite(value)) return 1;
  return Math.max(1, Math.min(IGNITION_MAX_MINUTES, Math.round(value)));
}

function preferredUnit(minutes: number): IgnitionDurationUnit {
  if (minutes >= 1_440 && minutes % 1_440 === 0) return 'days';
  if (minutes >= 60 && minutes % 60 === 0) return 'hours';
  return 'minutes';
}

function amountFor(minutes: number, unit: IgnitionDurationUnit): number {
  const settings = UNIT_SETTINGS[unit];
  return Math.max(1, Math.min(settings.maximum, Math.round(minutes / settings.factor)));
}

function presetLabel(minutes: number): string {
  const labels: Readonly<Record<number, string>> = {
    15: '15 min',
    30: '30 min',
    60: '1 hour',
    120: '2 hours',
    480: '8 hours',
    1_440: '1 day',
    4_320: '3 days',
    10_080: '7 days',
  };
  return labels[minutes] ?? formatIgnitionDuration(minutes * 60);
}

export function IgnitionDurationEditor({
  minutes,
  disabled,
  onChange,
}: IgnitionDurationEditorProps) {
  const [unit, setUnit] = useState<IgnitionDurationUnit>(() => preferredUnit(minutes));
  const [amountText, setAmountText] = useState(() => String(amountFor(minutes, unit)));
  const settings = UNIT_SETTINGS[unit];
  const amount = amountFor(minutes, unit);

  useEffect(() => {
    setAmountText(String(amountFor(minutes, unit)));
  }, [minutes, unit]);

  const applyAmount = (nextAmount: number) => {
    const candidate = Number.isFinite(nextAmount) ? nextAmount : amount;
    const bounded = Math.max(1, Math.min(settings.maximum, Math.round(candidate)));
    setAmountText(String(bounded));
    onChange(normalizeIgnitionMinutes(bounded * settings.factor));
  };

  const selectPreset = (presetMinutes: number) => {
    const nextUnit = preferredUnit(presetMinutes);
    setUnit(nextUnit);
    setAmountText(String(amountFor(presetMinutes, nextUnit)));
    onChange(presetMinutes);
  };

  return (
    <section className="ignition-duration-editor" aria-labelledby="ignition-duration-title">
      <header>
        <div>
          <h3 id="ignition-duration-title">Pause for</h3>
          <p>Choose a preset, type an exact duration, or use the slider.</p>
        </div>
        <strong>{formatIgnitionDuration(minutes * 60)}</strong>
      </header>

      <div className="ignition-duration-presets" role="group" aria-label="Common pause durations">
        {IGNITION_DURATION_PRESETS.map((preset) => (
          <button
            type="button"
            key={preset}
            disabled={disabled}
            aria-pressed={minutes === preset}
            onClick={() => selectPreset(preset)}
          >
            {presetLabel(preset)}
          </button>
        ))}
      </div>

      <div className="ignition-duration-fields">
        <label>
          <span>Amount</span>
          <input
            type="number"
            min={1}
            max={settings.maximum}
            step={1}
            inputMode="numeric"
            value={amountText}
            disabled={disabled}
            onChange={(event) => {
              const text = event.currentTarget.value;
              setAmountText(text);
              if (text === '') return;
              const parsed = Number(text);
              if (Number.isInteger(parsed) && parsed >= 1 && parsed <= settings.maximum) {
                onChange(normalizeIgnitionMinutes(parsed * settings.factor));
              }
            }}
            onBlur={() => applyAmount(Number(amountText))}
          />
        </label>
        <label>
          <span>Unit</span>
          <select
            value={unit}
            disabled={disabled}
            onChange={(event) => {
              const nextUnit = event.currentTarget.value as IgnitionDurationUnit;
              const nextAmount = amountFor(minutes, nextUnit);
              setUnit(nextUnit);
              setAmountText(String(nextAmount));
              onChange(normalizeIgnitionMinutes(nextAmount * UNIT_SETTINGS[nextUnit].factor));
            }}
          >
            <option value="minutes">minutes</option>
            <option value="hours">hours</option>
            <option value="days">days</option>
          </select>
        </label>
      </div>

      <label className="ignition-duration-slider">
        <span>{settings.label}</span>
        <input
          type="range"
          min={1}
          max={settings.maximum}
          step={1}
          value={amount}
          disabled={disabled}
          aria-label={`Pause duration in ${settings.label}`}
          onInput={(event) => applyAmount(Number(event.currentTarget.value))}
        />
        <output>{amount}</output>
      </label>
    </section>
  );
}

export function ignitionPauseConfirmation(minutes: number): string {
  return `Pause ignition monitoring for ${formatIgnitionDuration(minutes * 60)}?\n\nIgnition-on actions will be suppressed until the deadline.`;
}
