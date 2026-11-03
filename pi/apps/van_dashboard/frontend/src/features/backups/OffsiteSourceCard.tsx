import { useCallback } from 'react';

import { formatBytes, formatDuration, formatRelativeTime, formatSpeed } from '../../utils/format';
import { loadOffsiteRates, type OffsiteRun } from './offsite';
import {
  OFFSITE_HINTS,
  OFFSITE_RATE_WORDS,
  OFFSITE_STATE_LABELS,
  offsiteSourceCopy,
} from './offsiteContent';
import { OffsiteRow as Row } from './OffsiteRow';
import { OFFSITE_TONES, shortId, type OffsiteSourceView } from './offsitePresentation';
import { RateHistoryButton } from './UploadSpeedSheet';

export function OffsiteProgress({ run, title }: { run: OffsiteRun; title: string }) {
  const percent =
    run.percent ??
    (run.bytesTotal ? Math.min(100, (100 * (run.bytesDone ?? 0)) / run.bytesTotal) : null);
  return (
    <>
      {percent !== null && (
        <div
          className="backup-progress"
          role="progressbar"
          aria-label={`${title} progress`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(percent)}
        >
          <span style={{ width: `${percent}%` }} />
        </div>
      )}
      <small>
        {formatBytes(run.bytesDone)}
        {run.bytesTotal === null ? '' : ` of ${formatBytes(run.bytesTotal)}`}
        {run.rate !== null && ` · ${formatSpeed(run.rate)} logical`}
        {run.eta !== null && ` · ${formatDuration(run.eta)} left`}
      </small>
    </>
  );
}

export function OffsiteSourceCard({
  view,
  run,
  remote,
}: {
  view: OffsiteSourceView;
  run: OffsiteRun;
  remote: boolean;
}) {
  const { name, contents } = offsiteSourceCopy(view.source);
  const { outcome } = view;
  const load = useCallback(
    (signal: AbortSignal) => loadOffsiteRates(view.source, signal),
    [view.source],
  );
  const receipt = shortId(outcome?.receipt ?? null);
  return (
    <article
      className={`backup-offsite-source backup-offsite-source--${OFFSITE_TONES[view.state]}`}
    >
      <header>
        <h4>{name}</h4>
        <span className="backup-offsite__state">{OFFSITE_STATE_LABELS[view.state]}</span>
      </header>
      <p className="backup-offsite-source__contents">{contents}</p>
      {view.live && <OffsiteProgress run={run} title={name} />}
      {outcome?.cause && <p className="backup-offsite__cause">{outcome.cause}</p>}
      {outcome && outcome.result !== 'running' && (
        <dl>
          <Row label="Processed" hint={OFFSITE_HINTS.logical}>
            {formatBytes(outcome.logicalBytesProcessed)}
          </Row>
          <Row label="Newly added" hint={OFFSITE_HINTS.packed}>
            {formatBytes(outcome.newlyAddedPackedBytes)}
          </Row>
          <Row label="Duration">{formatDuration(outcome.durationSeconds)}</Row>
          <Row label="Finished">
            {view.finished === null ? '—' : formatRelativeTime(view.finished)}
          </Row>
          <Row label="Snapshot">{shortId(outcome.snapshot) ?? '—'}</Row>
          {remote && receipt && <Row label="Receipt">{receipt}</Row>}
        </dl>
      )}
      {view.lastOk && (
        <small>
          Last OK{' '}
          {view.lastOk.finished === null ? 'earlier' : formatRelativeTime(view.lastOk.finished)}
          {shortId(view.lastOk.snapshot) && ` · ${shortId(view.lastOk.snapshot)}`}
        </small>
      )}
      <div className="backup-offsite-source__footer">
        <RateHistoryButton
          words={OFFSITE_RATE_WORDS}
          load={load}
          title={name}
          running={view.live}
        />
      </div>
    </article>
  );
}
