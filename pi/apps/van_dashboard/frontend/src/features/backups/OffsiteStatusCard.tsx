import { useState } from 'react';
import { createPortal } from 'react-dom';

import { formatBytes, formatDuration, formatRelativeTime, formatSpeed } from '../../utils/format';
import { DiskStatsSheet } from '../storage/DiskStatsSheet';
import type { OffsiteEvidence, OffsiteStatus } from './offsite';
import {
  OFFSITE_HEADLINES,
  OFFSITE_HINTS,
  OFFSITE_STATE_LABELS,
  OFFSITE_TARGET_COPY,
  offsiteSourceCopy,
} from './offsiteContent';
import { OffsiteRow as Row } from './OffsiteRow';
import { OFFSITE_TONES } from './offsitePresentation';
import { OffsiteProgress } from './OffsiteSourceCard';

function evidenceText(evidence: OffsiteEvidence | null): string {
  if (!evidence) return 'Never';
  const when = formatRelativeTime(evidence.finished);
  return evidence.result === 'ok'
    ? `OK · ${when}`
    : `Failed · ${when}${evidence.cause ? ` · ${evidence.cause}` : ''}`;
}

function DriveIoButton({ label }: { label: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        className="secondary-button"
        aria-label={`${label} drive I/O history`}
        title={OFFSITE_HINTS.driveIo}
        onClick={() => setOpen(true)}
      >
        Drive I/O
      </button>
      {/* On body so card rules never reach the modal, as the upload-speed sheet does. */}
      {open &&
        createPortal(
          <DiskStatsSheet label={label} onClose={() => setOpen(false)} />,
          document.body,
        )}
    </>
  );
}

export function OffsiteStatusCard({ status }: { status: OffsiteStatus }) {
  const { run, targetDrive } = status;
  const target = OFFSITE_TARGET_COPY[run.targetKind];
  const remote = run.targetKind === 'remote';
  const drive = !remote && run.label ? targetDrive : null;
  return (
    <article className={`backup-offsite-main backup-offsite-source--${OFFSITE_TONES[run.result]}`}>
      <header>
        <h4>{OFFSITE_HEADLINES[run.result]}</h4>
        <span className="backup-offsite__state">{OFFSITE_STATE_LABELS[run.result]}</span>
      </header>
      <p>{run.label ? target.line(run.label) : 'No offsite target has been used yet.'}</p>
      {run.result === 'running' && (
        <>
          <p className="backup-offsite-source__contents">
            {run.source
              ? `${target.activeVerb} ${offsiteSourceCopy(run.source).name}`
              : target.activeVerb}
          </p>
          <OffsiteProgress run={run} title="Offsite backup" />
        </>
      )}
      {run.cause && <p className="backup-offsite__cause">{run.cause}</p>}
      {run.cleanupErrors.map((error) => (
        <p className="backup-offsite__cause" key={error}>
          Cleanup: {error}
        </p>
      ))}
      <dl>
        {run.result === 'running' && run.rate !== null && (
          <Row label="Logical rate" hint={OFFSITE_HINTS.rate}>
            {formatSpeed(run.rate)}
          </Row>
        )}
        {run.result === 'running' && run.eta !== null && (
          <Row label="Time left">{formatDuration(run.eta)}</Row>
        )}
        {run.started !== null && <Row label="Started">{formatRelativeTime(run.started)}</Row>}
        {run.result === 'running' && run.updated !== null && (
          <Row label="Updated">{formatRelativeTime(run.updated)}</Row>
        )}
        {run.result !== 'running' && run.finished !== null && (
          <Row label="Finished">
            {formatRelativeTime(run.finished)} · {formatDuration(run.durationSeconds)}
          </Row>
        )}
        {drive && (
          <Row label="Target drive">
            {drive.mounted
              ? `${formatBytes(drive.usedBytes)} of ${formatBytes(drive.totalBytes)} used`
              : 'Not mounted'}
          </Row>
        )}
        {remote && <Row label="Last check">{evidenceText(status.check)}</Row>}
        {remote && <Row label="Last restore test">{evidenceText(status.restoreTest)}</Row>}
      </dl>
      {run.notificationError && <small>Notification failed: {run.notificationError}</small>}
      {drive && <DriveIoButton label={drive.label} />}
      {!status.historySupported && <small>{OFFSITE_HINTS.historyUnavailable}</small>}
    </article>
  );
}
