import { useEffect, useId, useRef, useState } from 'react';

import { previewDealWatchSchedule } from './api';
import type { DealWatchSchedule } from './types';

export const SCHEDULE_PREVIEW_DELAY_MS = 250;

export interface DealWatchScheduleFormProps {
  schedule: DealWatchSchedule;
  disabled: boolean;
  onSave: (expression: string) => Promise<boolean>;
}

export function normalizeCronExpression(expression: string): string {
  return expression.trim().replace(/\s+/g, ' ');
}

export function DealWatchScheduleForm({ schedule, disabled, onSave }: DealWatchScheduleFormProps) {
  const inputId = useId();
  const [expression, setExpression] = useState(schedule.expression);
  const [preview, setPreview] = useState(schedule);
  const [parsing, setParsing] = useState(false);
  const requestId = useRef(0);

  useEffect(() => {
    setExpression(schedule.expression);
    setPreview(schedule);
    setParsing(false);
  }, [schedule.description, schedule.error, schedule.errorCode, schedule.expression]);

  useEffect(() => {
    const normalized = normalizeCronExpression(expression);
    const savedExpression = normalizeCronExpression(schedule.expression);
    const currentRequest = ++requestId.current;

    if (normalized === savedExpression) {
      setPreview(schedule);
      setParsing(false);
      return;
    }
    if (!normalized) {
      setPreview({
        expression: '',
        description: '',
        error: 'Enter a cron expression',
        errorCode: 'parse',
      });
      setParsing(false);
      return;
    }

    setParsing(true);
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void previewDealWatchSchedule(normalized, controller.signal)
        .then((nextPreview) => {
          if (requestId.current !== currentRequest) return;
          setPreview(nextPreview);
          setParsing(false);
        })
        .catch((reason: unknown) => {
          if (controller.signal.aborted || requestId.current !== currentRequest) return;
          setPreview({
            expression: normalized,
            description: '',
            error: reason instanceof Error ? reason.message : String(reason),
            errorCode: 'parse',
          });
          setParsing(false);
        });
    }, SCHEDULE_PREVIEW_DELAY_MS);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [expression, schedule]);

  const normalized = normalizeCronExpression(expression);
  const canSave =
    !disabled &&
    !parsing &&
    preview.error === null &&
    normalized === preview.expression &&
    normalized !== normalizeCronExpression(schedule.expression);

  return (
    <form
      className="deal-watch-schedule-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (canSave) void onSave(normalized);
      }}
    >
      <label htmlFor={inputId}>Cron schedule</label>
      <div>
        <input
          id={inputId}
          type="text"
          maxLength={160}
          autoComplete="off"
          autoCapitalize="none"
          spellCheck={false}
          placeholder="0 10,15,20 * * *"
          value={expression}
          disabled={disabled}
          required
          onChange={(event) => setExpression(event.target.value)}
        />
        <button type="submit" disabled={!canSave}>
          Save schedule
        </button>
      </div>
      <small
        className={preview.error ? 'deal-watch-schedule-form__error' : undefined}
        aria-live="polite"
      >
        {parsing ? 'Parsing schedule…' : (preview.error ?? preview.description)}
      </small>
    </form>
  );
}
