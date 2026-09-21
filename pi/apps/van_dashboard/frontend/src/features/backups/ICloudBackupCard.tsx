import { formatBytes, formatDuration, formatRelativeTime } from '../../utils/format';
import { iCloudPhaseLabel, iCloudProgress } from './icloud';
import type { ICloudStatus } from './icloud';

function date(value: number | null): string {
  return value === null ? 'Not scheduled' : new Date(value * 1000).toLocaleString();
}

export function ICloudBackupCard({ status }: { status: ICloudStatus }) {
  if (!status.available)
    return (
      <section className="backups-sheet__section" aria-label="Pi offsite · iCloud">
        <h3>Pi offsite · iCloud</h3>
        <p className="error-message">
          Local iCloud status is unavailable. Other backups are still shown.
        </p>
      </section>
    );
  const p = iCloudProgress(status);
  const showProgress =
    status.running ||
    (status.generation !== null && status.phase !== 'not due' && status.phase !== 'complete');
  return (
    <section className="backups-sheet__section backup-icloud" aria-labelledby="backup-icloud-title">
      <div className="backups-sheet__heading">
        <div>
          <h3 id="backup-icloud-title">Pi offsite · iCloud</h3>
          <p>
            Encrypted Borg recovery copy · every {status.intervalDays} days · keep{' '}
            {status.keepGenerations} verified copies.
          </p>
        </div>
        <span
          className={`backup-icloud__badge ${status.running ? 'backup-icloud__badge--running' : status.attention ? 'backup-icloud__badge--attention' : ''}`}
        >
          {iCloudPhaseLabel(status.phase)}
        </span>
      </div>
      <p>{status.message}</p>
      {status.lastSuccessAt === null ? (
        <p className="backup-icloud__warning">No verified offsite backup yet.</p>
      ) : (
        <p>
          Last verified{' '}
          <time
            dateTime={new Date(status.lastSuccessAt * 1000).toISOString()}
            title={date(status.lastSuccessAt)}
          >
            {formatRelativeTime(status.lastSuccessAt)}
          </time>
        </p>
      )}
      {showProgress && (
        <div className="backup-icloud__progress">
          <strong>
            {p.verification ? 'Download verification' : 'Upload estimate'}
            {p.percent === null ? '' : ` · ${p.percent.toFixed(1)}%`}
          </strong>
          {p.percent !== null ? (
            <>
              <div
                className="backup-progress"
                role="progressbar"
                aria-label={
                  p.verification ? 'iCloud download verification' : 'iCloud upload estimate'
                }
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Number(p.percent.toFixed(1))}
              >
                <span style={{ width: `${p.percent}%` }} />
              </div>
              <small>
                {formatBytes(p.done)} of {formatBytes(p.total)}
                {p.verification
                  ? ` · ${status.progress.verifiedFiles ?? 0}/${status.progress.verificationTotalFiles ?? '—'} files verified`
                  : ' · includes files saved by earlier attempts'}
              </small>
              {p.verification && status.running && (status.progress.currentFileBytes ?? 0) > 0 && (
                <small>
                  Checking current file: {formatBytes(status.progress.currentFileBytes)} downloaded;
                  credited after its checksum passes.
                </small>
              )}
            </>
          ) : (
            <small>Progress will update when the worker reaches the transfer phase.</small>
          )}
          <small>
            {status.running ? 'Worker update' : 'Last saved progress'}:{' '}
            {status.updatedAt === null ? 'not recorded yet' : formatRelativeTime(status.updatedAt)}
            {status.progressStale ? ' · waiting for fresh counters' : ''}
          </small>
          {!p.verification && (
            <small>
              Upload percentage is size-based, not verification. Every file must pass a downloaded
              SHA-256 check before this is a verified recovery copy.
            </small>
          )}
        </div>
      )}
      <dl className="backup-icloud__schedule">
        <div>
          <dt>Next retry / eligibility check</dt>
          <dd>
            {status.running && status.nextCheckAt === null
              ? 'After the current attempt ends'
              : date(status.nextCheckAt)}
          </dd>
        </div>
        {status.nextDueAt !== null && (
          <div>
            <dt>Next weekly copy due</dt>
            <dd>{date(status.nextDueAt)}</dd>
          </div>
        )}
      </dl>
      <p className="backup-icloud__note">
        Starlink routes are excluded. Local backups take priority. This is Pi recovery only; Mac
        Time Machine is not copied to iCloud.
      </p>
      <details className="backup-icloud__history">
        <summary>Attempt history ({status.attempts.length})</summary>
        <p>
          Up to 100 attempts, newest first. Tracking{' '}
          {status.historyStartedAt === null
            ? 'starts with the next attempt'
            : `since ${date(status.historyStartedAt)}`}
          ; older attempts remain in the service journal.
        </p>
        {status.attempts.length === 0 ? (
          <p>No recorded attempts yet.</p>
        ) : (
          <ol>
            {status.attempts.map((attempt) => (
              <li key={attempt.id}>
                <div>
                  <strong>{iCloudPhaseLabel(attempt.phase)}</strong> ·{' '}
                  <time dateTime={new Date(attempt.startedAt * 1000).toISOString()}>
                    {date(attempt.startedAt)}
                  </time>
                  {attempt.endedAt !== null &&
                    ` · ${formatDuration(attempt.endedAt - attempt.startedAt)}`}
                </div>
                <p>{attempt.message}</p>
                <small>
                  Reached: {iCloudPhaseLabel(attempt.workPhase)}
                  {attempt.progress.verifiedFiles !== null
                    ? ` · ${attempt.progress.verifiedFiles} files verified`
                    : attempt.workPhase !== 'checking' && attempt.progress.commandBytes !== null
                      ? ` · ${formatBytes(attempt.progress.commandBytes)} transferred this attempt`
                      : ''}
                </small>
                {attempt.verifiedAt !== null && (
                  <small>Recovery copy verified {date(attempt.verifiedAt)}</small>
                )}
                {attempt.generation && <code>{attempt.generation}</code>}
              </li>
            ))}
          </ol>
        )}
      </details>
      {status.verifiedGenerations.length > 0 && (
        <details className="backup-icloud__history">
          <summary>Verified recovery history ({status.verifiedGenerations.length})</summary>
          <p>Historical successes; older cloud copies may have been removed by retention.</p>
          <ol>
            {status.verifiedGenerations.map((entry) => (
              <li key={entry.generation}>
                <time>{date(entry.completedAt)}</time>
                <code>{entry.generation}</code>
              </li>
            ))}
          </ol>
        </details>
      )}
    </section>
  );
}
