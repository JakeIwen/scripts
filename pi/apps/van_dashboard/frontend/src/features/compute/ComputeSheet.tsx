import { BottomSheet } from '../../components/BottomSheet';
import type { PollingState } from '../../hooks/usePollingResource';
import { formatBytes } from '../../utils/format';
import { ComputeJobList } from './ComputeJobList';
import { ComputeRangeSelector } from './ComputeRangeSelector';
import { computeRangeLabel, formatComputeDuration } from './presentation';
import type { ComputeRange, ComputeReport } from './types';
import { useComputeExplorer } from './useComputeExplorer';
import './compute.css';

export interface ComputeSheetProps {
  open: boolean;
  onClose: () => void;
  rangeHours: ComputeRange;
  onRangeChange: (range: ComputeRange) => void;
  resource: PollingState<ComputeReport>;
}

function localReasonLabel(reason: string): string {
  return reason
    .split('-')
    .map((part) => (part ? `${part[0]?.toUpperCase()}${part.slice(1)}` : ''))
    .join(' ');
}

export function ComputeSheet({
  open,
  onClose,
  rangeHours,
  onRangeChange,
  resource,
}: ComputeSheetProps) {
  const report = resource.data;
  const explorer = useComputeExplorer(rangeHours, report?.jobs ?? []);
  const awaitingSelectedRange = report !== null && report.rangeHours !== rangeHours;
  const rangeStatus = awaitingSelectedRange
    ? `Loading ${computeRangeLabel(rangeHours)}…`
    : report
      ? `${computeRangeLabel(report.rangeHours)} · ${report.jobs.length} recent jobs`
      : resource.initialLoading
        ? 'Loading…'
        : 'No report';
  const overview = report
    ? [
        [
          'Offloaded jobs',
          String(report.summary.jobs),
          `${report.summary.succeeded} succeeded · ${report.summary.failed} failed`,
        ],
        [
          'Mac CPU',
          formatComputeDuration(report.summary.macCpuSeconds),
          `${formatComputeDuration(report.summary.macWallSeconds)} active time`,
        ],
        [
          'Peak job memory',
          formatBytes(report.summary.peakResidentBytes),
          `${report.summary.telemetryJobs} measured jobs`,
        ],
        [
          'Input processed',
          formatBytes(report.summary.inputBytes),
          `${formatBytes(report.summary.resultBytes)} returned`,
        ],
        [
          'Eligible local work',
          String(report.eligibleLocalWork.events),
          `${formatComputeDuration(report.eligibleLocalWork.cpuSeconds)} Pi CPU`,
        ],
        [
          'Queue delay',
          formatComputeDuration(report.summary.averageQueueSeconds),
          'Average completed-job delay',
        ],
      ]
    : [];

  return (
    <BottomSheet
      open={open}
      title="M4 Compute Offload"
      description="Measured remote work, eligible Pi-local work, and recent queue outcomes."
      onClose={onClose}
    >
      <div className="compute-sheet__toolbar">
        <ComputeRangeSelector
          value={rangeHours}
          disabled={resource.refreshing}
          onChange={onRangeChange}
        />
        <div>
          <span aria-live="polite">{rangeStatus}</span>
          <button
            className="secondary-button"
            type="button"
            disabled={resource.refreshing}
            onClick={() => void resource.refresh()}
          >
            {resource.refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      </div>

      {resource.error && <p className="error-message">{resource.error.message}</p>}

      <div className="compute-sheet__stack" aria-busy={resource.refreshing}>
        <section className="compute-overview" aria-label="Compute totals">
          {overview.length ? (
            overview.map(([label, value, detail]) => (
              <article key={label}>
                <span>{label}</span>
                <strong>{value}</strong>
                <small>{detail}</small>
              </article>
            ))
          ) : (
            <p className="compute-sheet__empty">Waiting for compute metrics…</p>
          )}
        </section>

        <section className="panel-card">
          <header className="compute-section-heading">
            <div>
              <h3>Eligible work left on Pi</h3>
              <p>Recorded broker fallbacks and manually identified eligible work.</p>
            </div>
            <strong>{report?.eligibleLocalWork.events ?? 0} recorded</strong>
          </header>
          <div className="compute-local-reasons">
            {report?.eligibleLocalWork.reasons.length ? (
              report.eligibleLocalWork.reasons.map((reason) => (
                <span key={reason.reason}>
                  <strong>{reason.events}</strong>
                  {localReasonLabel(reason.reason)}
                </span>
              ))
            ) : (
              <p className="compute-sheet__empty">No eligible local work recorded.</p>
            )}
          </div>
        </section>

        <section className="panel-card">
          <header className="compute-section-heading">
            <div>
              <h3>Work by task</h3>
              <p>Completed offloaded jobs in the selected range.</p>
            </div>
          </header>
          <div className="compute-task-list">
            {report?.tasks.length ? (
              report.tasks.map((task) => (
                <button
                  type="button"
                  className="compute-task-filter"
                  aria-pressed={explorer.selectedTask === task.task}
                  onClick={() => explorer.toggleTask(task.task)}
                  key={task.task}
                >
                  <span>
                    <strong>{task.task}</strong>
                    <small>
                      {task.jobs} jobs · {task.succeeded} succeeded · {task.failed} failed
                    </small>
                  </span>
                  <span>
                    {formatComputeDuration(task.cpuSeconds)} CPU
                    <small>
                      {formatBytes(task.peakResidentBytes)} peak · {formatBytes(task.inputBytes)}{' '}
                      input
                    </small>
                  </span>
                </button>
              ))
            ) : (
              <p className="compute-sheet__empty">No completed task totals in this range.</p>
            )}
          </div>
        </section>

        <section className="panel-card">
          <header className="compute-section-heading">
            <div>
              <h3>Recent queue and offloaded jobs</h3>
              <p>Read-only outcome and resource summary; retained output is not loaded here.</p>
            </div>
            <strong>{explorer.jobs.length} shown</strong>
          </header>
          {explorer.selectedTask && (
            <p className="compute-filter-status">
              {explorer.taskLoading
                ? 'Loading older matches…'
                : `Filtered to ${explorer.selectedTask}`}
              {explorer.taskError ? ` · ${explorer.taskError}` : ''}
            </p>
          )}
          <ComputeJobList explorer={explorer} />
        </section>

        {report?.measurementNote && (
          <p className="compute-measurement-note">{report.measurementNote}</p>
        )}
        <aside className="compute-sheet__read-only">
          Compute diagnostics are read-only. Retained output is loaded only when expanded.
        </aside>
      </div>
    </BottomSheet>
  );
}
