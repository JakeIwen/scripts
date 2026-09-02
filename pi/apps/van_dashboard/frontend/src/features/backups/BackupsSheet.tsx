import type { PollingState } from '../../hooks/usePollingResource';
import { BottomSheet } from '../../components/BottomSheet';
import { formatBytes, formatRelativeTime } from '../../utils/format';
import type { BackupControls } from './controls';
import {
  backupOperationLabel,
  evidenceLabel,
  progressLabel,
  timeMachineLabel,
} from './presentation';
import type {
  BackupEvidence,
  BackupSettings,
  BackupStatus,
  HotspareStatus,
  TimeMachineStatus,
} from './types';
import './backups.css';

export interface BackupsSheetProps {
  open: boolean;
  onClose: () => void;
  resource: PollingState<BackupStatus>;
  controls: BackupControls;
}

function EvidenceCard({
  title,
  evidence,
  kind,
  status,
  controls,
}: {
  title: string;
  evidence: BackupEvidence;
  kind?: 'borg' | 'exfat';
  status: BackupStatus;
  controls: BackupControls;
}) {
  const progress = progressLabel(evidence.progress);
  const progressPercent = evidence.progress?.progressPercent;
  const operationRunning = status.operation.status === 'running';
  const stopRunning = status.stop.status === 'running';
  const stopping = kind !== undefined && stopRunning && status.stop.kind === kind;
  const anyBackupRunning =
    status.borg.running || status.exfatSnapshot.running || status.openwrt.running;
  const actionDisabled = evidence.running
    ? controls.blocked || stopRunning
    : controls.blocked || operationRunning || stopRunning || anyBackupRunning;
  return (
    <article className={`backup-evidence ${evidence.stale ? 'backup-evidence--stale' : ''}`}>
      <header>
        <h4>{title}</h4>
        <span>
          {stopping
            ? 'Stopping'
            : evidence.running
              ? 'Running'
              : evidence.stale
                ? 'Stale'
                : 'Current'}
        </span>
      </header>
      <p>{evidenceLabel(evidence)}</p>
      {stopping ? <small>Stopping gracefully…</small> : progress && <small>{progress}</small>}
      {progressPercent !== null && progressPercent !== undefined && (
        <div
          className="backup-progress"
          role="progressbar"
          aria-label={`${title} progress`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={progressPercent}
        >
          <span style={{ width: `${progressPercent}%` }} />
        </div>
      )}
      {kind && (
        <>
          <small className="backup-action-detail">
            {evidence.running || stopping
              ? kind === 'borg' && evidence.progress?.phase === 'cloning'
                ? 'Stops gracefully. The in-progress hotspare may be incomplete and will not be marked current.'
                : kind === 'borg'
                  ? 'Stops gracefully. Unfinished Borg work is discarded and a scheduled run may retry later.'
                  : 'Stops gracefully, retains the partial snapshot for retry, then unmounts and spins down hdd1tb.'
              : kind === 'borg'
                ? 'Backs up vanpi with Borg, syncs media, applies retention, and refreshes due hotspares.'
                : 'Mounts hdd1tb, snapshots EXFAT512 with hard links, applies retention, then unmounts and spins down.'}
          </small>
          <button
            className={
              evidence.running || stopping
                ? 'danger-button backup-action'
                : 'primary-button backup-action'
            }
            type="button"
            disabled={actionDisabled}
            onClick={() =>
              void (evidence.running ? controls.stop(kind, status) : controls.start(kind))
            }
          >
            {stopping ? 'Stopping…' : evidence.running ? 'Stop gracefully' : 'Run now'}
          </button>
        </>
      )}
    </article>
  );
}

function HotspareCard({
  card,
  status,
  controls,
}: {
  card: HotspareStatus;
  status: BackupStatus;
  controls: BackupControls;
}) {
  const state = !card.attached
    ? 'Not attached'
    : card.mounted
      ? 'Mounted · unsafe to clone'
      : card.due
        ? 'Clone due'
        : 'Current';
  return (
    <article className={`backup-hotspare ${card.stale ? 'backup-hotspare--stale' : ''}`}>
      <header>
        <h4>{card.label}</h4>
        <span>{state}</span>
      </header>
      <dl>
        <div>
          <dt>Cadence</dt>
          <dd>{card.intervalDays} days</dd>
        </div>
        <div>
          <dt>Last clone</dt>
          <dd>{card.lastCloneAt ? formatRelativeTime(card.lastCloneAt) : 'Never'}</dd>
        </div>
        <div>
          <dt>Device</dt>
          <dd>{card.device ?? 'Not attached'}</dd>
        </div>
        <div>
          <dt>Size</dt>
          <dd>{card.sizeBytes === null ? '—' : formatBytes(card.sizeBytes)}</dd>
        </div>
      </dl>
      <button
        className="primary-button backup-action"
        type="button"
        disabled={
          controls.blocked ||
          status.operation.status === 'running' ||
          status.stop.status === 'running' ||
          status.borg.running ||
          status.exfatSnapshot.running ||
          status.openwrt.running ||
          !card.attached ||
          card.mounted
        }
        onClick={() => void controls.clone(card)}
      >
        Clone now
      </button>
    </article>
  );
}

function TimeMachineCard({ status }: { status: TimeMachineStatus }) {
  return (
    <article className={`backup-time-machine ${status.error ? 'backup-time-machine--error' : ''}`}>
      <header>
        <h4>Time Machine · {status.device}</h4>
        <span>{status.running ? 'Running' : 'Idle'}</span>
      </header>
      <p>{timeMachineLabel(status)}</p>
      {status.running && status.bytesCopied !== null && (
        <small>
          {formatBytes(status.bytesCopied)}
          {status.totalBytes === null ? '' : ` of ${formatBytes(status.totalBytes)}`}
        </small>
      )}
      <div className="backup-snapshot-list">
        {status.snapshots.slice(0, 8).map((timestamp, index) => (
          <span key={`${timestamp}-${index}`}>
            {index === 0 ? 'Latest' : `Previous ${index}`} · {formatRelativeTime(timestamp)}
          </span>
        ))}
      </div>
    </article>
  );
}

function CloneCardCapacity({
  settings,
  controls,
}: {
  settings: BackupSettings;
  controls: BackupControls;
}) {
  return (
    <div className="backup-capacity-control">
      <label>
        <span>Clone card size</span>
        <select
          aria-label="Bootable clone card capacity"
          value={settings.cloneCardNominalGb}
          disabled={controls.blocked}
          onChange={(event) => {
            const selected = settings.cloneCardNominalGbOptions.find(
              (option) => String(option) === event.currentTarget.value,
            );
            if (selected !== undefined) void controls.setCloneCardNominalGb(selected);
          }}
        >
          {settings.cloneCardNominalGbOptions.map((option) => (
            <option value={option} key={option}>
              {option} GB
            </option>
          ))}
        </select>
      </label>
      <small>Warn above {settings.rootUsedMaxGib} GiB root usage</small>
    </div>
  );
}

export function BackupsSheet({ open, onClose, resource, controls }: BackupsSheetProps) {
  const status = resource.data;
  return (
    <BottomSheet
      open={open}
      title="Backups"
      description="Freshness, runtime progress, hotspares, and Time Machine evidence."
      onClose={onClose}
    >
      <header className="backups-sheet__summary">
        <span>{status ? backupOperationLabel(status) : 'Loading backup evidence…'}</span>
        <button
          className="secondary-button"
          type="button"
          disabled={resource.refreshing || controls.running || !open}
          onClick={() => void resource.refresh()}
        >
          {resource.refreshing ? 'Refreshing…' : 'Refresh'}
        </button>
      </header>
      {resource.error && <p className="error-message">{resource.error.message}</p>}
      {(controls.lastError || controls.lastMessage) && (
        <p className={controls.lastError ? 'error-message' : 'backup-control-message'}>
          {controls.lastError ?? controls.lastMessage}
        </p>
      )}

      <section className="backups-sheet__section" aria-labelledby="backup-evidence-title">
        <div className="backups-sheet__heading">
          <div>
            <h3 id="backup-evidence-title">Backup evidence</h3>
            <p>Freshness stamps and live process evidence.</p>
          </div>
          <span>{status?.health ?? 'No data'}</span>
        </div>
        <div className="backup-evidence-grid">
          {status ? (
            <>
              <EvidenceCard
                title="Vanpi Borg"
                evidence={status.borg}
                kind="borg"
                status={status}
                controls={controls}
              />
              <EvidenceCard
                title="EXFAT512 snapshot"
                evidence={status.exfatSnapshot}
                kind="exfat"
                status={status}
                controls={controls}
              />
              <EvidenceCard
                title="OpenWrt export"
                evidence={status.openwrt}
                status={status}
                controls={controls}
              />
              <TimeMachineCard status={status.timeMachine} />
            </>
          ) : (
            <p className="backups-empty">Loading evidence…</p>
          )}
        </div>
      </section>

      <section className="backups-sheet__section" aria-labelledby="backup-hotspares-title">
        <div className="backups-sheet__heading">
          <div>
            <h3 id="backup-hotspares-title">Bootable hotspares</h3>
            <p>Configured labels, cadence, attachment, and clone age.</p>
          </div>
          {status ? (
            <CloneCardCapacity settings={status.settings} controls={controls} />
          ) : (
            <span>Loading capacity…</span>
          )}
        </div>
        <div className="backup-hotspare-grid">
          {status?.hotswaps.length ? (
            status.hotswaps.map((card) => (
              <HotspareCard card={card} status={status} controls={controls} key={card.label} />
            ))
          ) : (
            <p className="backups-empty">No configured hotspares were returned.</p>
          )}
        </div>
      </section>
    </BottomSheet>
  );
}
