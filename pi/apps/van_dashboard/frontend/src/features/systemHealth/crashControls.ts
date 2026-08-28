import { useCallback, useState } from 'react';

import { useSingleFlightAction } from '../../hooks/useSingleFlightAction';
import { analyzePreviousCrash } from './api';
import type { CrashAnalysisResult, CrashHistory } from './types';

export type CrashAnalysisNotice = (message: string, tone: 'normal' | 'error') => void;

export interface CrashAnalysisControls {
  running: boolean;
  result: CrashAnalysisResult | null;
  error: string | null;
  analyze: () => Promise<void>;
}

function messageFrom(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

export function useCrashAnalysis(
  refreshHistory: () => Promise<CrashHistory | null>,
  notify?: CrashAnalysisNotice,
): CrashAnalysisControls {
  const { running, run } = useSingleFlightAction();
  const [result, setResult] = useState<CrashAnalysisResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const analyze = useCallback(async (): Promise<void> => {
    await run(async () => {
      setError(null);
      try {
        const next = await analyzePreviousCrash();
        setResult(next);
        notify?.(
          next.saved
            ? 'Crash analysis saved'
            : 'Crash analysis complete; no prior boot was available to save',
          'normal',
        );
      } catch (reason) {
        const message = messageFrom(reason);
        setError(message);
        notify?.(message, 'error');
      } finally {
        await refreshHistory();
      }
    });
  }, [notify, refreshHistory, run]);

  return { running, result, error, analyze };
}
