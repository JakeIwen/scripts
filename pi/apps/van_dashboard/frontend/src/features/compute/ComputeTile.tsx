import { KeyValueList } from '../../components/KeyValueList';
import { StatusPill } from '../../components/StatusPill';
import { Tile } from '../../components/Tile';
import { formatBytes } from '../../utils/format';
import {
  computeRangeLabel,
  computeStatusLabel,
  computeTone,
  formatComputeDuration,
} from './presentation';
import type { ComputeReport } from './types';
import './compute.css';

export interface ComputeTileProps {
  report: ComputeReport | null;
  error: Error | null;
  refreshing: boolean;
  onOpen: () => void;
}

function workerLabel(report: ComputeReport): string {
  const remoteWorkers = report.status.workers
    .filter((worker) => worker.placement !== 'pi-local')
    .sort((left, right) => (left.ageSeconds ?? Infinity) - (right.ageSeconds ?? Infinity));
  const worker = remoteWorkers[0];
  if (!worker) return report.status.available ? 'Remote worker ready' : 'No heartbeat';
  return `${worker.name} · ${worker.available ? 'ready' : 'offline'}`;
}

export function ComputeTile({ report, error, refreshing, onOpen }: ComputeTileProps) {
  const tone = computeTone(report, error);
  const slots =
    report?.status.slotsTotal === null || report?.status.slotsTotal === undefined
      ? 'Awaiting heartbeat'
      : `${report.status.slotsBusy ?? 0} / ${report.status.slotsTotal} busy`;
  const queue = report
    ? `${report.status.running} Mac · ${report.status.localRunning} Pi · ${report.status.queued} queued`
    : '—';
  const items = [
    { label: 'Worker', value: report ? workerLabel(report) : '—' },
    { label: 'Slots', value: slots },
    { label: 'Queue', value: queue },
    {
      label: 'Mac CPU delivered',
      value: formatComputeDuration(report?.summary.macCpuSeconds),
    },
    { label: 'Peak job memory', value: formatBytes(report?.summary.peakResidentBytes) },
    {
      label: 'Eligible left on Pi',
      value: report ? `${report.eligibleLocalWork.events} recorded` : '—',
    },
  ];

  return (
    <Tile
      icon="🧠"
      title="M4 Compute"
      summary={
        error && !report
          ? error.message
          : report
            ? `${report.summary.jobs} offloaded job${report.summary.jobs === 1 ? '' : 's'} in ${computeRangeLabel(report.rangeHours)}`
            : 'Checking offloaded analysis…'
      }
      status={<StatusPill tone={tone}>{computeStatusLabel(report, refreshing)}</StatusPill>}
      tone={tone}
      onClick={onOpen}
      ariaLabel="Open M4 compute details"
      className="compute-tile"
    >
      <KeyValueList items={items} />
      <p className="compute-tile__read-only">Metrics and queue history · read-only</p>
    </Tile>
  );
}
