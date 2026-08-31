export const SYSTEM_HEALTH_RANGES = [6, 24, 168, 720] as const;

export type SystemHealthRange = (typeof SYSTEM_HEALTH_RANGES)[number];
export type SystemHealthLevel = 'good' | 'warning' | 'critical' | 'unknown';
export type SystemEventSeverity = 'info' | 'warning' | 'critical' | 'unknown';

export interface ProjectedMetric {
  value: number | null;
  observedAt: number | null;
}

export interface CurrentSystemSample {
  observedAt: number | null;
  cpuPercent: number | null;
  memoryPercent: number | null;
  temperatureCelsius: number | null;
  armMegahertz: number | null;
  throttleWord: string | null;
  activeThrottleFlags: string[];
  occurredThrottleFlags: string[];
  networkReceiveBytesPerSecond: number | null;
  networkTransmitBytesPerSecond: number | null;
  diskReadBytesPerSecond: number | null;
  diskWriteBytesPerSecond: number | null;
  diskBusyPercent: number | null;
}

export interface SystemHealthEvidence {
  undervoltageEpisodes: number;
  undervoltageSeconds: number;
  usbFailures: number;
  storageErrors: number;
}

export interface RepeatProcessOffender {
  name: string;
  peakCount: number;
  cpuPeakCount: number;
  memoryPeakCount: number;
  maximumCpuPercent: number | null;
  maximumResidentBytes: number | null;
  lastSeenAt: number | null;
}

export interface SystemHealthEvent {
  timestamp: number;
  severity: SystemEventSeverity;
  category: string;
  summary: string;
  message: string;
}

export interface SystemHealthReport {
  generatedAt: number | null;
  rangeHours: SystemHealthRange;
  available: boolean;
  stale: boolean;
  sampleAgeSeconds: number | null;
  level: SystemHealthLevel;
  headline: string;
  findings: string[];
  nextSteps: string[];
  evidence: SystemHealthEvidence;
  eventCount: number;
  current: CurrentSystemSample | null;
  peaks: {
    cpuPercent: ProjectedMetric;
    memoryPercent: ProjectedMetric;
    temperatureCelsius: ProjectedMetric;
    networkReceiveBytesPerSecond: ProjectedMetric;
    networkTransmitBytesPerSecond: ProjectedMetric;
    diskReadBytesPerSecond: ProjectedMetric;
    diskWriteBytesPerSecond: ProjectedMetric;
    diskBusyPercent: ProjectedMetric;
  };
  repeatOffenders: RepeatProcessOffender[];
  events: SystemHealthEvent[];
}

export interface CrashTimelineEntry {
  timestamp: number;
  severity: SystemEventSeverity;
  summary: string;
  message: string;
}

export interface CrashAnalysis {
  available: boolean;
  level: SystemHealthLevel;
  headline: string;
  findings: string[];
  previousBootEndedAt: number | null;
  counts: Record<string, number>;
  timeline: CrashTimelineEntry[];
}

export interface CrashComparison {
  previousHeadline: string;
  previousLevel: SystemHealthLevel;
  countDeltas: Record<string, number>;
}

export interface CrashHistoryItem extends CrashAnalysis {
  id: number;
  analyzedAt: number;
}

export interface CrashHistory {
  items: CrashHistoryItem[];
}

export interface CrashAnalysisResult {
  analysis: CrashAnalysis;
  comparison: CrashComparison | null;
  saved: boolean;
}
