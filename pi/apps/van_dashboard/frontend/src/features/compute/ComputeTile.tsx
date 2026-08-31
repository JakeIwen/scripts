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

function compactAge(timestamp: number | null): string {
  if (timestamp === null) return 'never';
  const seconds = Math.max(0, Date.now() / 1_000 - timestamp);
  return seconds < 90 ? `${Math.round(seconds)}s ago` : `${Math.round(seconds / 60)}m ago`;
}

function workerLabel(report: ComputeReport): string {
  const remoteWorkers = report.status.workers.filter((worker) => worker.placement !== 'pi-local');
  const capacityWorkers = remoteWorkers.filter(
    (worker) => worker.available && worker.slotsTotal !== null,
  );
  const worker = (capacityWorkers.length ? capacityWorkers : remoteWorkers)
    .slice()
    .sort((left, right) => (left.ageSeconds ?? Infinity) - (right.ageSeconds ?? Infinity))[0];
  if (report.status.available) {
    return `${worker?.name ?? 'Mac'} · ${report.status.running ? 'working' : 'ready'}`;
  }
  return worker?.seenAt ? `Last seen ${compactAge(worker.seenAt)}` : 'No heartbeat';
}

export function ComputeTile({ report, error, refreshing, onOpen }: ComputeTileProps) {
  const tone = computeTone(report, error);
  const slots = !report
    ? '—'
    : report.status.slotsTotal === null || report.status.slotsTotal === undefined
      ? 'Awaiting scheduler heartbeat'
      : `${report.status.slotsBusy ?? 0} / ${report.status.slotsTotal} busy · ${report.status.slotsAvailable ?? 0} free`;
  const queue = report
    ? report.status.running || report.status.localRunning || report.status.queued
      ? `${report.status.running} Mac running · ${report.status.localRunning} Pi fallback · ${report.status.queued} queued`
      : 'Empty'
    : '—';
  const items = [
    { label: 'Worker', value: report ? workerLabel(report) : '—' },
    { label: 'Slots', value: slots },
    { label: 'Queue', value: queue },
    {
      label: 'Mac CPU delivered',
      value: report?.summary.telemetryJobs
        ? formatComputeDuration(report.summary.macCpuSeconds)
        : report
          ? 'Awaiting telemetry job'
          : '—',
    },
    {
      label: 'Peak job memory',
      value: report?.summary.telemetryJobs
        ? formatBytes(report.summary.peakResidentBytes)
        : report
          ? 'Awaiting telemetry job'
          : '—',
    },
    {
      label: 'Eligible left on Pi',
      value: report
        ? report.eligibleLocalWork.events
          ? `${report.eligibleLocalWork.events} event${report.eligibleLocalWork.events === 1 ? '' : 's'} · ${formatComputeDuration(report.eligibleLocalWork.cpuSeconds)} CPU`
          : 'None recorded'
        : '—',
    },
  ];

  return (
    <Tile
      icon="🧠"
      title="M4 Compute"
      summary={
        error && !report
          ? 'Compute metrics unavailable'
          : report
            ? report.summary.jobs
              ? `${report.summary.jobs} job${report.summary.jobs === 1 ? '' : 's'} offloaded in ${computeRangeLabel(report.rangeHours)}`
              : 'No completed jobs in selected range'
            : 'Checking offloaded analysis…'
      }
      status={
        <StatusPill tone={tone} dot={false}>
          {computeStatusLabel(report, refreshing)}
        </StatusPill>
      }
      tone={tone}
      onClick={onOpen}
      ariaLabel="Open M4 compute details"
      className="compute-tile"
    >
      <KeyValueList items={items} />
    </Tile>
  );
}
