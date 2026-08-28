import { getJson, postForm } from '../../api/client';
import { decodeCrashAnalysis, decodeCrashHistory } from './crashDecoder';
import { decodeSystemHealthReport } from './decoder';
import type {
  CrashAnalysisResult,
  CrashHistory,
  SystemHealthRange,
  SystemHealthReport,
} from './types';

export async function fetchSystemHealthReport(
  rangeHours: SystemHealthRange,
  signal?: AbortSignal,
): Promise<SystemHealthReport> {
  const payload = await getJson(`/api/system-monitor?hours=${rangeHours}`, signal);
  return decodeSystemHealthReport(payload, rangeHours);
}

export async function fetchCrashHistory(signal?: AbortSignal): Promise<CrashHistory> {
  return decodeCrashHistory(await getJson('/api/system-monitor/crashes', signal));
}

export async function analyzePreviousCrash(): Promise<CrashAnalysisResult> {
  return decodeCrashAnalysis(await postForm('/api/system-monitor/crash-analysis'));
}
