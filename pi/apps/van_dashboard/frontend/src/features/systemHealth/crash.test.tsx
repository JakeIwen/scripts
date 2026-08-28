import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { analyzePreviousCrash } from './api';
import { useCrashAnalysis } from './crashControls';
import { decodeCrashAnalysis, decodeCrashHistory } from './crashDecoder';

vi.mock('./api', () => ({ analyzePreviousCrash: vi.fn() }));
const analyzeMock = vi.mocked(analyzePreviousCrash);

function analysisResult() {
  return {
    analysis: {
      available: true,
      level: 'warning' as const,
      headline: 'Previous boot ended without clean shutdown evidence',
      findings: ['No normal shutdown marker was retained.'],
      previousBootEndedAt: 1_700_000_000,
      counts: { usb_reset: 2 },
      timeline: [],
    },
    comparison: null,
    saved: true,
  };
}

beforeEach(() => analyzeMock.mockResolvedValue(analysisResult()));

describe('crash analysis parity', () => {
  it('projects saved history and bounded analysis fields', () => {
    const history = decodeCrashHistory({
      ok: true,
      history: [
        {
          id: 7,
          analyzed_at: 1_700_000_100,
          level: 'warning',
          headline: 'Abrupt previous shutdown',
          findings: ['No clean shutdown marker.'],
          counts: { usb_reset: 2 },
          previous_boot: { ended_at: 1_700_000_000 },
          report: {
            analysis: {
              available: true,
              timeline: [
                {
                  timestamp: 1_699_999_900,
                  severity: 'warning',
                  summary: 'USB reset',
                  message: 'reset',
                },
              ],
            },
          },
        },
      ],
    });
    const analysis = decodeCrashAnalysis({
      ok: true,
      saved: true,
      comparison: {
        previous_headline: 'Earlier crash',
        previous_level: 'critical',
        count_deltas: { usb_reset: -1 },
      },
      analysis: {
        available: true,
        level: 'warning',
        headline: 'Abrupt previous shutdown',
        findings: ['No clean shutdown marker.'],
        previous_boot: { ended_at: 1_700_000_000 },
        counts: { usb_reset: 2 },
        timeline: [],
      },
    });

    expect(history.items[0]).toMatchObject({ id: 7, counts: { usb_reset: 2 } });
    expect(history.items[0]?.timeline[0]?.summary).toBe('USB reset');
    expect(analysis).toMatchObject({ saved: true, comparison: { previousLevel: 'critical' } });
  });

  it('prevents duplicate analysis and refreshes history after success', async () => {
    let finish: ((value: ReturnType<typeof analysisResult>) => void) | undefined;
    analyzeMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const refresh = vi.fn().mockResolvedValue({ items: [] });
    const { result } = renderHook(() => useCrashAnalysis(refresh));
    let first: Promise<void> | undefined;
    let duplicate: Promise<void> | undefined;

    act(() => {
      first = result.current.analyze();
      duplicate = result.current.analyze();
    });
    expect(analyzeMock).toHaveBeenCalledTimes(1);
    await act(async () => {
      finish?.(analysisResult());
      await first;
      await duplicate;
    });
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(result.current.result?.saved).toBe(true);
  });

  it('does not retry failure and still refreshes authoritative history', async () => {
    analyzeMock.mockRejectedValueOnce(new Error('analysis connection lost'));
    const refresh = vi.fn().mockResolvedValue({ items: [] });
    const { result } = renderHook(() => useCrashAnalysis(refresh));

    await act(async () => result.current.analyze());

    expect(analyzeMock).toHaveBeenCalledTimes(1);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(result.current.error).toBe('analysis connection lost');
  });
});
