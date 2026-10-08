import { useEffect, useState } from 'react';
import { BottomSheet } from '../../components/BottomSheet';
import { iCloudPhaseLabel, type ICloudBackupKind, type ICloudPhase } from './icloud';
import { loadAttemptDetails, type ICloudAttemptDetails } from './attemptDetails';
import './attemptDetails.css';

interface AttemptMessageProps {
  message: string;
  phase: ICloudPhase;
  kind: ICloudBackupKind;
  id?: string;
}

export function ICloudAttemptMessage({ message, phase, kind, id = 'latest' }: AttemptMessageProps) {
  const [open, setOpen] = useState(false);
  const hasDetails = ['error', 'authentication_required', 'interrupted'].includes(phase);
  const summary = message.startsWith('Attempt failed;') ? 'Attempt failed;' : message;
  return (
    <>
      <p>
        {summary}
        {hasDetails && (
          <>
            {' '}
            <button
              className="backup-attempt-details-link"
              type="button"
              onClick={() => setOpen(true)}
            >
              details
            </button>
            .
          </>
        )}
      </p>
      {open && <AttemptPopup kind={kind} id={id} onClose={() => setOpen(false)} />}
    </>
  );
}

function AttemptPopup({
  kind,
  id,
  onClose,
}: {
  kind: ICloudBackupKind;
  id: string;
  onClose: () => void;
}) {
  const [details, setDetails] = useState<ICloudAttemptDetails | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setDetails(null);
    setError(null);
    loadAttemptDetails(kind, id, controller.signal).then(
      (value) => {
        if (!controller.signal.aborted) setDetails(value);
      },
      (failure: unknown) => {
        if (!controller.signal.aborted)
          setError(failure instanceof Error ? failure.message : 'Could not load attempt details.');
      },
    );
    return () => controller.abort();
  }, [kind, id, reload]);
  return (
    <BottomSheet
      open
      title="Backup attempt details"
      description={kind === 'time-machine' ? 'Mac Time Machine · iCloud' : 'Pi offsite · iCloud'}
      className="backup-attempt-details"
      onClose={onClose}
    >
      {error ? (
        <div role="alert">
          <p>{error}</p>
          <button type="button" className="button" onClick={() => setReload((value) => value + 1)}>
            Retry loading details
          </button>
        </div>
      ) : details ? (
        <AttemptEvidence details={details} />
      ) : (
        <p role="status">Loading attempt details…</p>
      )}
    </BottomSheet>
  );
}

function AttemptEvidence({ details }: { details: ICloudAttemptDetails }) {
  const { attempt, entries, note } = details;
  return (
    <>
      <dl className="backup-icloud__schedule">
        <div>
          <dt>Started</dt>
          <dd>{new Date(attempt.startedAt * 1000).toLocaleString()}</dd>
        </div>
        <div>
          <dt>Ended</dt>
          <dd>
            {attempt.endedAt === null
              ? 'Not recorded'
              : new Date(attempt.endedAt * 1000).toLocaleString()}
          </dd>
        </div>
        <div>
          <dt>Result</dt>
          <dd>{iCloudPhaseLabel(attempt.phase)}</dd>
        </div>
        <div>
          <dt>Stage reached</dt>
          <dd>{iCloudPhaseLabel(attempt.workPhase)}</dd>
        </div>
      </dl>
      {entries.length > 0 && (
        <ol className="backup-attempt-details__entries">
          {entries.map((entry, index) => (
            <li key={`${entry.at}-${index}`}>
              <time>{new Date(entry.at * 1000).toLocaleString()}</time>
              <pre>{entry.message}</pre>
              <p>{entry.explanation}</p>
            </li>
          ))}
        </ol>
      )}
      {note && <p role="status">{note}</p>}
      {entries.length === 0 && !attempt.message.startsWith('Attempt failed;') && (
        <p>{attempt.message}</p>
      )}
      {attempt.generation && (
        <p className="backup-attempt-details__generation">
          Backup ID: <code>{attempt.generation}</code>
        </p>
      )}
    </>
  );
}
