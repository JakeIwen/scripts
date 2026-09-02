export type BackupHealth = 'running' | 'attention' | 'good';
export type BackupOperationStatus = 'idle' | 'running' | 'complete' | 'error' | 'stopped';
export type BackupOperationKind = 'clone' | 'borg' | 'exfat';
export type BackupStopStatus = 'idle' | 'running' | 'complete' | 'error';
export type BackupStopKind = 'borg' | 'exfat';
export type CloneCardNominalGb = 32 | 64 | 128 | 256;

export interface BackupSettings {
  cloneCardNominalGb: CloneCardNominalGb;
  rootUsedMaxGib: number;
  cloneCardNominalGbOptions: CloneCardNominalGb[];
}

export interface BackupProgress {
  phase: string;
  detail: string;
  startedAt: number;
  updatedAt: number;
  elapsedSeconds: number;
  bytesProcessed: number | null;
  progressPercent: number | null;
  filesTransferred: number | null;
  filesRemaining: number | null;
  fileListTotal: number | null;
}

export interface BackupEvidence {
  lastSuccessAt: number | null;
  staleHours: number;
  stale: boolean;
  running: boolean;
  progress: BackupProgress | null;
}

export interface HotspareStatus {
  label: string;
  intervalDays: number;
  attached: boolean;
  device: string | null;
  sizeBytes: number | null;
  mounted: boolean;
  mountpoints: string[];
  lastCloneAt: number | null;
  due: boolean;
  stale: boolean;
}

export interface TimeMachineStatus {
  device: string;
  available: boolean;
  lastBackupAt: number | null;
  snapshots: number[];
  running: boolean;
  progressPercent: number | null;
  bytesCopied: number | null;
  totalBytes: number | null;
  updatedAt: number | null;
  error: string | null;
}

export interface BackupOperation {
  status: BackupOperationStatus;
  kind: BackupOperationKind | null;
  target: string | null;
  startedAt: number | null;
  completedAt: number | null;
  error: string | null;
}

export interface BackupStopOperation {
  status: BackupStopStatus;
  kind: BackupStopKind | null;
  startedAt: number | null;
  completedAt: number | null;
  error: string | null;
}

export interface BackupStatus {
  checkedAt: number;
  health: BackupHealth;
  settings: BackupSettings;
  borg: BackupEvidence;
  exfatSnapshot: BackupEvidence;
  openwrt: BackupEvidence;
  hotswaps: HotspareStatus[];
  timeMachine: TimeMachineStatus;
  operation: BackupOperation;
  stop: BackupStopOperation;
}

export interface BackupMutationResult {
  message: string;
  backups: BackupStatus;
}
