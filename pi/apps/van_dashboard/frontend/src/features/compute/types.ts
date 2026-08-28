export const COMPUTE_RANGES = [6, 24, 168, 720] as const;

export type ComputeRange = (typeof COMPUTE_RANGES)[number];
export type ComputeJobState = 'queued' | 'running' | 'done' | 'failed';

export interface ComputeWorker {
  name: string;
  placement: string;
  available: boolean;
  seenAt: number | null;
  ageSeconds: number | null;
  slotsTotal: number | null;
  slotsBusy: number | null;
  slotsAvailable: number | null;
}

export interface ComputeStatus {
  configured: boolean | null;
  available: boolean;
  queued: number;
  running: number;
  localRunning: number;
  slotsTotal: number | null;
  slotsBusy: number | null;
  slotsAvailable: number | null;
  workers: ComputeWorker[];
}

export interface ComputeSummary {
  jobs: number;
  succeeded: number;
  failed: number;
  telemetryJobs: number;
  macCpuSeconds: number;
  macWallSeconds: number;
  peakResidentBytes: number;
  inputBytes: number;
  resultBytes: number;
  averageQueueSeconds: number | null;
  lastFinishedAt: number | null;
}

export interface ComputeTaskSummary {
  task: string;
  jobs: number;
  succeeded: number;
  failed: number;
  telemetryJobs: number;
  cpuSeconds: number;
  wallSeconds: number;
  peakResidentBytes: number;
  inputBytes: number;
}

export interface ComputeJobSummary {
  id: string;
  task: string;
  state: ComputeJobState;
  worker: string | null;
  placement: string;
  failureSummary: string | null;
  submittedAt: number | null;
  startedAt: number | null;
  finishedAt: number | null;
  queueSeconds: number | null;
  activeSeconds: number;
  cpuSeconds: number;
  peakResidentBytes: number;
  inputBytes: number;
  telemetryAvailable: boolean;
}

export interface ComputeLocalReason {
  reason: string;
  events: number;
}

export interface EligibleLocalWork {
  events: number;
  cpuSeconds: number;
  wallSeconds: number;
  peakResidentBytes: number;
  inputBytes: number;
  reasons: ComputeLocalReason[];
}

export interface ComputeReport {
  generatedAt: number | null;
  rangeHours: ComputeRange;
  status: ComputeStatus;
  summary: ComputeSummary;
  tasks: ComputeTaskSummary[];
  jobs: ComputeJobSummary[];
  eligibleLocalWork: EligibleLocalWork;
  measurementNote: string | null;
}

export interface ComputeTaskJobs {
  rangeHours: ComputeRange;
  task: string;
  matchingJobs: number;
  truncated: boolean;
  jobs: ComputeJobSummary[];
}

export interface ComputeDiagnosticText {
  text: string | null;
  truncated: boolean;
}

export interface ComputeJobDiagnostics {
  failureClassification: string | null;
  workerError: ComputeDiagnosticText;
  resourceLimit: ComputeDiagnosticText;
  resourceMonitorError: ComputeDiagnosticText;
  timedOut: boolean;
  interrupted: boolean;
}

export interface ComputeOutputExcerpt {
  available: boolean;
  bytes: number;
  excerpt: string;
  truncated: boolean;
  excerptFrom: 'none' | 'full' | 'tail';
}

export interface ComputeJobDetails {
  job: ComputeJobSummary;
  exitCode: number | null;
  diagnostics: ComputeJobDiagnostics;
  stdout: ComputeOutputExcerpt;
  stderr: ComputeOutputExcerpt;
}
