import { getJson } from '../../api/client';
import {
  arrayValue,
  booleanValue,
  nullableNumber,
  nullableString,
  objectValue,
  stringValue,
  type JsonObject,
} from '../../api/validation';
import { loadRateSamples, type SpeedSample } from './uploadSpeeds';

/** Mirrors Result in pi/scripts/backup/offsite_config.py and RESULTS in van_dashboard_offsite.py. */
export const OFFSITE_RESULTS = ['running', 'ok', 'failed', 'interrupted', 'not-run'] as const;
export type OffsiteResult = (typeof OFFSITE_RESULTS)[number];
export type OffsiteTargetKind = 'local' | 'remote';

export interface OffsiteOutcome {
  result: OffsiteResult;
  cause: string | null;
  snapshot: string | null;
  receipt: string | null;
  logicalBytesProcessed: number | null;
  newlyAddedPackedBytes: number | null;
  durationSeconds: number | null;
  /** Epoch seconds; only history entries carry it. */
  finished: number | null;
}

export interface OffsiteRun {
  result: OffsiteResult;
  label: string | null;
  source: string | null;
  bytesDone: number | null;
  bytesTotal: number | null;
  percent: number | null;
  rate: number | null;
  eta: number | null;
  started: number | null;
  updated: number | null;
  finished: number | null;
  durationSeconds: number | null;
  cause: string | null;
  cleanupErrors: string[];
  notificationError: string | null;
  targetKind: OffsiteTargetKind;
  sources: Record<string, OffsiteOutcome>;
}

export interface OffsiteHistoryEntry {
  latest: OffsiteOutcome;
  lastOk: OffsiteOutcome | null;
}

export interface OffsiteEvidence {
  result: 'ok' | 'failed';
  finished: number | null;
  durationSeconds: number | null;
  cause: string | null;
}

export interface OffsiteTargetDrive {
  label: string;
  mounted: boolean;
  usedBytes: number | null;
  totalBytes: number | null;
}

export interface OffsiteStatus {
  /** False while the Pi still runs the v1 script: no history, rates or evidence. */
  historySupported: boolean;
  run: OffsiteRun;
  /** label -> source -> outcomes. */
  history: Record<string, Record<string, OffsiteHistoryEntry>>;
  check: OffsiteEvidence | null;
  restoreTest: OffsiteEvidence | null;
  targetDrive: OffsiteTargetDrive | null;
}

function optionalNumber(value: unknown, label: string): number | null {
  return value === undefined ? null : nullableNumber(value, label);
}

function optionalText(value: unknown, label: string): string | null {
  return value === undefined ? null : nullableString(value, label);
}

function result(value: unknown, label: string): OffsiteResult {
  const text = stringValue(value, label);
  if (!(OFFSITE_RESULTS as readonly string[]).includes(text)) {
    throw new TypeError(`${label} has an unsupported value: ${text}`);
  }
  return text as OffsiteResult;
}

function decodeOutcome(value: unknown, label: string): OffsiteOutcome {
  const row = objectValue(value, label);
  return {
    result: result(row.result, `${label}.result`),
    cause: optionalText(row.cause, `${label}.cause`),
    snapshot: optionalText(row.snapshot, `${label}.snapshot`),
    receipt: optionalText(row.receipt, `${label}.receipt`),
    logicalBytesProcessed: optionalNumber(
      row.logical_bytes_processed,
      `${label}.logical_bytes_processed`,
    ),
    newlyAddedPackedBytes: optionalNumber(
      row.newly_added_packed_bytes,
      `${label}.newly_added_packed_bytes`,
    ),
    durationSeconds: optionalNumber(row.duration_seconds, `${label}.duration_seconds`),
    finished: optionalNumber(row.finished, `${label}.finished`),
  };
}

function decodeRun(value: unknown): OffsiteRun {
  const row = objectValue(value, 'offsite status');
  const targetKind = stringValue(row.target_kind, 'offsite status.target_kind');
  if (targetKind !== 'local' && targetKind !== 'remote') {
    throw new TypeError(`offsite status.target_kind has an unsupported value: ${targetKind}`);
  }
  const sources = objectValue(row.sources ?? {}, 'offsite status.sources');
  return {
    result: result(row.result, 'offsite status.result'),
    label: optionalText(row.label, 'offsite status.label'),
    source: optionalText(row.source, 'offsite status.source'),
    bytesDone: optionalNumber(row.bytes_done, 'offsite status.bytes_done'),
    bytesTotal: optionalNumber(row.bytes_total, 'offsite status.bytes_total'),
    percent: optionalNumber(row.percent, 'offsite status.percent'),
    rate: optionalNumber(row.rate, 'offsite status.rate'),
    eta: optionalNumber(row.eta, 'offsite status.eta'),
    started: optionalNumber(row.started, 'offsite status.started'),
    updated: optionalNumber(row.updated, 'offsite status.updated'),
    finished: optionalNumber(row.finished, 'offsite status.finished'),
    durationSeconds: optionalNumber(row.duration_seconds, 'offsite status.duration_seconds'),
    cause: optionalText(row.cause, 'offsite status.cause'),
    cleanupErrors: arrayValue(row.cleanup_errors ?? [], 'offsite status.cleanup_errors').map(
      (item) => stringValue(item, 'offsite status.cleanup_errors item'),
    ),
    notificationError: optionalText(row.notification_error, 'offsite status.notification_error'),
    targetKind,
    sources: Object.fromEntries(
      Object.entries(sources).map(([name, outcome]) => [
        name,
        decodeOutcome(outcome, `offsite status.sources.${name}`),
      ]),
    ),
  };
}

function decodeHistory(value: unknown): OffsiteStatus['history'] {
  const labels = objectValue(value ?? {}, 'offsite history');
  return Object.fromEntries(
    Object.entries(labels).map(([label, sources]) => [
      label,
      Object.fromEntries(
        Object.entries(objectValue(sources, `offsite history.${label}`)).map(([source, entry]) => {
          const row: JsonObject = objectValue(entry, `offsite history.${label}.${source}`);
          return [
            source,
            {
              latest: decodeOutcome(row.latest, `offsite history.${label}.${source}.latest`),
              lastOk:
                row.last_ok === null || row.last_ok === undefined
                  ? null
                  : decodeOutcome(row.last_ok, `offsite history.${label}.${source}.last_ok`),
            },
          ];
        }),
      ),
    ]),
  );
}

function decodeEvidence(value: unknown, label: string): OffsiteEvidence | null {
  if (value === null || value === undefined) return null;
  const row = objectValue(value, label);
  const outcome = stringValue(row.result, `${label}.result`);
  if (outcome !== 'ok' && outcome !== 'failed') {
    throw new TypeError(`${label}.result has an unsupported value: ${outcome}`);
  }
  return {
    result: outcome,
    finished: optionalNumber(row.finished, `${label}.finished`),
    durationSeconds: optionalNumber(row.duration_seconds, `${label}.duration_seconds`),
    cause: optionalText(row.cause, `${label}.cause`),
  };
}

function decodeDrive(value: unknown): OffsiteTargetDrive | null {
  if (value === null || value === undefined) return null;
  const row = objectValue(value, 'offsite target drive');
  return {
    label: stringValue(row.label, 'offsite target drive.label'),
    mounted: booleanValue(row.mounted, 'offsite target drive.mounted'),
    usedBytes: optionalNumber(row.used_bytes, 'offsite target drive.used_bytes'),
    totalBytes: optionalNumber(row.total_bytes, 'offsite target drive.total_bytes'),
  };
}

export function decodeOffsiteResponse(payload: unknown): OffsiteStatus {
  const response = objectValue(payload, 'offsite response');
  if (response.ok !== true) throw new TypeError('offsite response.ok must be true');
  const supported = objectValue(response.supported ?? {}, 'offsite response.supported');
  const evidence = objectValue(response.evidence ?? {}, 'offsite response.evidence');
  return {
    historySupported: booleanValue(
      supported.history ?? false,
      'offsite response.supported.history',
    ),
    run: decodeRun(response.status),
    history: decodeHistory(response.history),
    check: decodeEvidence(evidence.check, 'offsite evidence.check'),
    restoreTest: decodeEvidence(evidence.restore_test, 'offsite evidence.restore_test'),
    targetDrive: decodeDrive(response.target_drive),
  };
}

export async function fetchOffsiteStatus(signal: AbortSignal): Promise<OffsiteStatus> {
  return decodeOffsiteResponse(await getJson('/api/backups/offsite', signal));
}

/** Samples for one source; empty while the Pi still lacks the v2 script (the route says `supported: false`). */
export async function loadOffsiteRates(
  source: string,
  signal: AbortSignal,
): Promise<SpeedSample[]> {
  return loadRateSamples(`/api/backups/offsite/${encodeURIComponent(source)}/rates`, signal);
}
