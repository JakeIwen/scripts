import { formatRelativeTime } from '../../utils/format';
import type { OffsiteOutcome, OffsiteResult, OffsiteStatus } from './offsite';
import { OFFSITE_SOURCE_ORDER, OFFSITE_TARGET_COPY, offsiteSourceCopy } from './offsiteContent';

export type OffsiteTone = 'running' | 'ok' | 'failed' | 'idle';

export const OFFSITE_TONES: Record<OffsiteResult | 'no-record', OffsiteTone> = {
  running: 'running',
  ok: 'ok',
  failed: 'failed',
  interrupted: 'failed',
  'not-run': 'idle',
  'no-record': 'idle',
};

export interface OffsiteSourceView {
  source: string;
  state: OffsiteResult | 'no-record';
  /** Set only while this source is the one running; progress comes from the live status. */
  live: boolean;
  outcome: OffsiteOutcome | null;
  finished: number | null;
  /** The last successful outcome, shown only when it differs from `outcome`. */
  lastOk: OffsiteOutcome | null;
}

/** Canonical sources first, in run order, then any source the Pi reports beyond them. */
export function offsiteSourceKeys(status: OffsiteStatus): string[] {
  const known = new Set<string>(OFFSITE_SOURCE_ORDER);
  const extras = new Set<string>();
  const label = status.run.label;
  for (const name of [
    ...Object.keys(status.run.sources),
    ...Object.keys((label && status.history[label]) || {}),
  ]) {
    if (!known.has(name)) extras.add(name);
  }
  return [...OFFSITE_SOURCE_ORDER, ...[...extras].sort()];
}

export function offsiteSourceView(status: OffsiteStatus, source: string): OffsiteSourceView {
  const { run } = status;
  const live = run.result === 'running' && run.source === source;
  const entry = run.label ? status.history[run.label]?.[source] : undefined;
  const runOutcome = run.sources[source] ?? null;
  // History outlives a run's `sources`, which only holds that run's selected sources.
  const outcome = entry?.latest ?? runOutcome;
  const lastOk = entry?.lastOk && entry.latest.result !== 'ok' ? entry.lastOk : null;
  const state: OffsiteSourceView['state'] = live
    ? 'running'
    : outcome
      ? outcome.result
      : status.historySupported
        ? 'not-run'
        : 'no-record';
  const finished =
    outcome?.finished ?? (runOutcome && run.result !== 'running' ? run.finished : null);
  return { source, state, live, outcome, finished, lastOk };
}

export interface OffsiteChip {
  text: string;
  tone: OffsiteTone;
}

/** Summary-row text, readable while the section is collapsed. */
export function offsiteChip(status: OffsiteStatus | null, failed: boolean): OffsiteChip {
  if (!status) return { text: failed ? 'Unavailable' : 'Loading', tone: 'idle' };
  const { run } = status;
  const target = OFFSITE_TARGET_COPY[run.targetKind];
  switch (run.result) {
    case 'running': {
      const percent = run.percent === null ? '' : ` ${Math.round(run.percent)}%`;
      const where = run.source ? ` · ${offsiteSourceCopy(run.source).name}` : '';
      return { text: `${target.activeVerb}${percent}${where}`, tone: 'running' };
    }
    case 'ok':
      return { text: `Last run OK · ${formatRelativeTime(run.finished)}`, tone: 'ok' };
    case 'failed':
      return { text: 'Failed', tone: 'failed' };
    case 'interrupted':
      return { text: 'Interrupted', tone: 'failed' };
    case 'not-run':
      return { text: 'Not run yet', tone: 'idle' };
  }
}

/** First eight characters of a snapshot or receipt id, the length restic shows. */
export function shortId(id: string | null): string | null {
  return id ? id.slice(0, 8) : null;
}
