import type {
  CrashAnalysis,
  CrashAnalysisResult,
  CrashComparison,
  CrashHistory,
  CrashHistoryItem,
  CrashTimelineEntry,
  SystemEventSeverity,
  SystemHealthLevel,
} from './types';

type UnknownRecord = Record<string, unknown>;

function record(value: unknown): UnknownRecord | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as UnknownRecord)
    : null;
}

function text(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

function number(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function integer(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isInteger(value) ? value : fallback;
}

function level(value: unknown): SystemHealthLevel {
  return value === 'good' || value === 'warning' || value === 'critical' || value === 'unknown'
    ? value
    : 'unknown';
}

function severity(value: unknown): SystemEventSeverity {
  return value === 'info' || value === 'warning' || value === 'critical' ? value : 'unknown';
}

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string')
    : [];
}

function numericRecord(value: unknown): Record<string, number> {
  const object = record(value);
  if (!object) return {};
  return Object.fromEntries(
    Object.entries(object).flatMap(([key, item]) => {
      const parsed = number(item);
      return parsed === null ? [] : [[key, parsed]];
    }),
  );
}

function timeline(value: unknown): CrashTimelineEntry[] {
  if (!Array.isArray(value)) return [];
  return value.slice(0, 80).flatMap((item) => {
    const row = record(item);
    const timestamp = number(row?.timestamp);
    if (!row || timestamp === null) return [];
    return [
      {
        timestamp,
        severity: severity(row.severity),
        summary: text(row.summary, text(row.source, 'Log')),
        message: text(row.message),
      },
    ];
  });
}

function projectAnalysis(value: unknown): CrashAnalysis {
  const analysis = record(value) ?? {};
  const previousBoot = record(analysis.previous_boot);
  return {
    available: analysis.available === true,
    level: level(analysis.level),
    headline: text(analysis.headline, 'Crash analysis unavailable'),
    findings: stringList(analysis.findings),
    previousBootEndedAt: number(previousBoot?.ended_at),
    counts: numericRecord(analysis.counts),
    timeline: timeline(analysis.timeline),
  };
}

function projectComparison(value: unknown): CrashComparison | null {
  const comparison = record(value);
  if (!comparison) return null;
  return {
    previousHeadline: text(comparison.previous_headline, 'Earlier analysis'),
    previousLevel: level(comparison.previous_level),
    countDeltas: numericRecord(comparison.count_deltas),
  };
}

export function decodeCrashHistory(value: unknown): CrashHistory {
  const response = record(value);
  if (!response || response.ok !== true || !Array.isArray(response.history)) {
    throw new TypeError('crash history response has an invalid schema');
  }
  const items = response.history.flatMap((item, index): CrashHistoryItem[] => {
    const row = record(item);
    if (!row) return [];
    const reportAnalysis = record(record(row.report)?.analysis);
    const projected = projectAnalysis(reportAnalysis ?? row);
    return [
      {
        ...projected,
        id: integer(row.id, index),
        analyzedAt: number(row.analyzed_at) ?? 0,
        level: level(row.level ?? projected.level),
        headline: text(row.headline, projected.headline),
        findings: stringList(row.findings).length ? stringList(row.findings) : projected.findings,
        counts: Object.keys(numericRecord(row.counts)).length
          ? numericRecord(row.counts)
          : projected.counts,
        previousBootEndedAt:
          number(record(row.previous_boot)?.ended_at) ?? projected.previousBootEndedAt,
      },
    ];
  });
  return { items };
}

export function decodeCrashAnalysis(value: unknown): CrashAnalysisResult {
  const response = record(value);
  if (!response || response.ok !== true || !record(response.analysis)) {
    throw new TypeError('crash analysis response has an invalid schema');
  }
  return {
    analysis: projectAnalysis(response.analysis),
    comparison: projectComparison(response.comparison),
    saved: response.saved === true,
  };
}
