import { useCallback, useEffect, useRef, useState } from 'react';

const SLIDER_DEBOUNCE_MS = 500;

interface LightingSliderProps {
  label: string;
  value: number;
  minimum: number;
  maximum: number;
  unit: string;
  disabled: boolean;
  displayValue?: string;
  className?: string;
  onCommit: (value: number) => Promise<boolean>;
}

/**
 * Show slider movement immediately, but commit only on release/keyboard/blur
 * or after a short idle debounce. React's range onChange fires for every input
 * event, so it is intentionally not used as the mutation boundary.
 */
export function LightingSlider({
  label,
  value,
  minimum,
  maximum,
  unit,
  disabled,
  displayValue,
  className = '',
  onCommit,
}: LightingSliderProps) {
  const [draft, setDraft] = useState(value);
  const committedRef = useRef(value);
  const timerRef = useRef<number | null>(null);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = null;
  }, []);

  useEffect(() => {
    committedRef.current = value;
    setDraft(value);
  }, [value]);

  useEffect(() => clearTimer, [clearTimer]);

  const commit = useCallback(
    async (next: number) => {
      clearTimer();
      if (disabled || next === committedRef.current) return;
      const previous = committedRef.current;
      committedRef.current = next;
      try {
        const accepted = await onCommit(next);
        if (!accepted) {
          committedRef.current = previous;
          setDraft(previous);
        }
      } catch {
        committedRef.current = previous;
        setDraft(previous);
      }
    },
    [clearTimer, disabled, onCommit],
  );

  const updateDraft = (next: number) => {
    setDraft(next);
    clearTimer();
    timerRef.current = window.setTimeout(() => void commit(next), SLIDER_DEBOUNCE_MS);
  };

  return (
    <label className={`lighting-slider-control ${className}`.trim()}>
      <span>{label}</span>
      <input
        type="range"
        min={minimum}
        max={maximum}
        value={draft}
        disabled={disabled}
        aria-label={label}
        onInput={(event) => updateDraft(Number(event.currentTarget.value))}
        onPointerUp={(event) => void commit(Number(event.currentTarget.value))}
        onKeyUp={(event) => void commit(Number(event.currentTarget.value))}
        onBlur={(event) => void commit(Number(event.currentTarget.value))}
      />
      <output>{displayValue ?? `${draft}${unit}`}</output>
    </label>
  );
}
