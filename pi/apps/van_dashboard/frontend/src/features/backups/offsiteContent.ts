import type { OffsiteResult, OffsiteTargetKind } from './offsite';
import type { RateHistoryWords } from './uploadSpeeds';

/** Run order of the canonical sources in pi/scripts/backup/offsite_config.py; extras sort after. */
export const OFFSITE_SOURCE_ORDER = ['exfat512', 'movingparts', 'borg', 'time-machine'] as const;

export const OFFSITE_SOURCE_COPY: Record<string, { name: string; contents: string }> = {
  exfat512: {
    name: 'EXFAT512',
    contents: 'Flash drive, minus van-tv and recorder data',
  },
  movingparts: { name: 'Moving Parts', contents: 'Jake, music and guitar tabs' },
  borg: { name: 'Borg repository', contents: 'The Pi’s Borg backup repository' },
  'time-machine': { name: 'Time Machine', contents: 'Encrypted MacBook backup image' },
};

export function offsiteSourceCopy(source: string): { name: string; contents: string } {
  return OFFSITE_SOURCE_COPY[source] ?? { name: source, contents: 'Additional backup source' };
}

export const OFFSITE_TARGET_COPY: Record<
  OffsiteTargetKind,
  { line: (label: string) => string; verb: string; activeVerb: string }
> = {
  local: { line: (label) => `Local seed · ${label}`, verb: 'Seed', activeVerb: 'Seeding' },
  remote: {
    line: (label) => `Synology over Tailscale · ${label}`,
    verb: 'Backup',
    activeVerb: 'Backing up',
  },
};

export const OFFSITE_HEADLINES: Record<OffsiteResult, string> = {
  running: 'Backup running',
  ok: 'Last run succeeded',
  failed: 'Last run failed',
  interrupted: 'Last run was interrupted',
  'not-run': 'No run recorded yet',
};

export const OFFSITE_STATE_LABELS: Record<OffsiteResult | 'no-record', string> = {
  running: 'Running',
  ok: 'OK',
  failed: 'Failed',
  interrupted: 'Interrupted',
  'not-run': 'Not run yet',
  'no-record': 'No record in current run',
};

export const OFFSITE_HINTS = {
  rate: 'Logical processing rate: source bytes restic has read and checked per second. It is not disk or network throughput, and already-stored data is processed quickly.',
  packed:
    'Compressed, deduplicated new data this backup wrote to the repository. It is usually far smaller than the source.',
  logical: 'Source bytes restic processed, before compression and deduplication.',
  historyUnavailable:
    'Per-source history and rate graphs appear once the Pi has the updated offsite script; until then only the current run is shown.',
  driveIo: 'Read, write and busy history of the target drive from the system monitor.',
} as const;

export const OFFSITE_RATE_WORDS: RateHistoryWords = {
  sheetTitle: 'Offsite processing rate',
  buttonLabel: 'Processing rate graph',
  noun: 'processing rate',
  activeLabel: 'Backing up',
  totalLabel: 'Processed',
  totalHint:
    'Estimated from the per-minute logical rates; counts source bytes restic read, not data stored or sent.',
  emptyText: 'History starts with the next run after the Pi update.',
};
