import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { previewDealWatchSchedule } from './api';
import { DealWatchScheduleForm, SCHEDULE_PREVIEW_DELAY_MS } from './DealWatchScheduleForm';
import type { DealWatchSchedule } from './types';

vi.mock('./api', () => ({ previewDealWatchSchedule: vi.fn() }));

const previewMock = vi.mocked(previewDealWatchSchedule);
const savedSchedule: DealWatchSchedule = {
  expression: '0 10,15,20 * * *',
  description: 'Current schedule',
  error: null,
  errorCode: null,
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((complete) => {
    resolve = complete;
  });
  return { promise, resolve };
}

describe('Deal Watch cron preview', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('ignores a stale response after a newer expression has been parsed', async () => {
    const first = deferred<DealWatchSchedule>();
    const second = deferred<DealWatchSchedule>();
    previewMock.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);

    render(
      <DealWatchScheduleForm
        schedule={savedSchedule}
        disabled={false}
        onSave={vi.fn().mockResolvedValue(true)}
      />,
    );
    const input = screen.getByLabelText('Cron schedule');

    fireEvent.change(input, { target: { value: '15 8 * * *' } });
    await act(async () => vi.advanceTimersByTimeAsync(SCHEDULE_PREVIEW_DELAY_MS));
    fireEvent.change(input, { target: { value: '30 9 * * *' } });
    await act(async () => vi.advanceTimersByTimeAsync(SCHEDULE_PREVIEW_DELAY_MS));

    await act(async () => {
      second.resolve({
        expression: '30 9 * * *',
        description: 'Newer description',
        error: null,
        errorCode: null,
      });
      await Promise.resolve();
    });
    expect(screen.getByText('Newer description')).toBeInTheDocument();

    await act(async () => {
      first.resolve({
        expression: '15 8 * * *',
        description: 'Stale description',
        error: null,
        errorCode: null,
      });
      await Promise.resolve();
    });
    expect(screen.queryByText('Stale description')).not.toBeInTheDocument();
    expect(screen.getByText('Newer description')).toBeInTheDocument();
  });

  it('shows a rate limit as a settled error without retrying', async () => {
    previewMock.mockResolvedValue({
      expression: '30 9 * * *',
      description: '',
      error: 'Cron parser rate limit reached',
      errorCode: 'rate_limit',
    });

    render(
      <DealWatchScheduleForm
        schedule={savedSchedule}
        disabled={false}
        onSave={vi.fn().mockResolvedValue(true)}
      />,
    );
    fireEvent.change(screen.getByLabelText('Cron schedule'), {
      target: { value: '30 9 * * *' },
    });
    await act(async () => vi.advanceTimersByTimeAsync(SCHEDULE_PREVIEW_DELAY_MS));

    expect(screen.getByText('Cron parser rate limit reached')).toBeInTheDocument();
    await act(async () => vi.advanceTimersByTimeAsync(60_000));
    expect(previewMock).toHaveBeenCalledOnce();
    expect(screen.getByRole('button', { name: 'Save schedule' })).toBeDisabled();
  });
});
