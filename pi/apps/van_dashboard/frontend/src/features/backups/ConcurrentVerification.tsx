import { formatBytes, formatPercent } from '../../utils/format';
import type { AvailableICloudStatus, VerificationActivity } from './icloud';

const activities: Record<VerificationActivity, string> = {
  connecting: 'Waiting for iCloud data',
  downloading: 'Downloading and checking',
  'network-check': 'Checking network route · transfer briefly paused',
  inventory: 'Checking available cloud chunks',
  waiting: 'Caught up with completed uploads',
};

export function ConcurrentVerification({ status }: { status: AvailableICloudStatus }) {
  const p = status.progress;
  if (!p.parallelVerification || status.lastWorkPhase !== 'uploading' || !p.verificationTotalBytes)
    return null;
  const percent = Math.min(100, ((p.verifiedBytes ?? 0) / p.verificationTotalBytes) * 100);
  const fresh =
    status.running &&
    status.phase === 'uploading' &&
    !status.progressStale &&
    p.verificationUpdatedAt != null &&
    Math.abs(Date.now() / 1000 - p.verificationUpdatedAt) <= 60;
  return (
    <div className="backup-icloud__progress">
      <strong>
        {status.running ? 'Download verification' : 'Saved verification'} ·{' '}
        {formatPercent(percent, 1)} · {formatBytes(p.verifiedBytes)} of{' '}
        {formatBytes(p.verificationTotalBytes)}
        {fresh && p.verificationBytesPerSecond != null && (
          <span title="Average download rate over the last minute, including connection and network-check waits.">
            {` · ${formatBytes(p.verificationBytesPerSecond)}/s average`}
          </span>
        )}
      </strong>
      <div
        className="backup-progress"
        role="progressbar"
        aria-label="Concurrent iCloud download verification"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Number(percent.toFixed(1))}
      >
        <span style={{ width: `${percent}%` }} />
      </div>
      <small>
        {p.verifiedFiles ?? 0}/{p.verificationTotalFiles ?? '—'} chunks checked
        {fresh && p.verificationActivity
          ? ` · ${activities[p.verificationActivity]}`
          : fresh && p.verificationWaiting
            ? ' · Caught up with completed uploads'
            : ''}
        {status.running && !fresh ? ' · Waiting for fresh verification counters' : ''}
        {fresh && (p.currentFileBytes ?? 0) > 0
          ? ` · Checking next chunk: ${formatBytes(p.currentFileBytes)} received`
          : ''}
      </small>
    </div>
  );
}
