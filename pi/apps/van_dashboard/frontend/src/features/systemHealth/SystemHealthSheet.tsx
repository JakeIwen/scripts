import { BottomSheet } from '../../components/BottomSheet';
import { KeyValueList } from '../../components/KeyValueList';
import type { PollingState } from '../../hooks/usePollingResource';
import { formatBytes, formatPercent, formatRelativeTime } from '../../utils/format';
import type { CrashAnalysisControls } from './crashControls';
import { HealthRangeSelector } from './HealthRangeSelector';
import { systemHealthRangeLabel } from './presentation';
import type {
  CrashAnalysis,
  CrashHistory,
  CrashHistoryItem,
  SystemHealthRange,
  SystemHealthReport,
} from './types';
import './systemHealth.css';

export interface SystemHealthSheetProps {
  open: boolean;
  onClose: () => void;
  rangeHours: SystemHealthRange;
  onRangeChange: (range: SystemHealthRange) => void;
  resource: PollingState<SystemHealthReport>;
  crashHistory: PollingState<CrashHistory>;
  crashAnalysis: CrashAnalysisControls;
}

function temperature(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${value.toFixed(1)} °C`;
}

function rate(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${formatBytes(value)}/s`;
}

function countLabel(value: string): string {
  return value.replaceAll('_', ' ').replace(/^./, (letter) => letter.toUpperCase());
}

function CrashDetails({ analysis }: { analysis: CrashAnalysis }) {
  const counts = Object.entries(analysis.counts).filter(([, count]) => count > 0);
  return (
    <>
      {analysis.findings.length > 0 && (
        <ul>
          {analysis.findings.map((finding) => (
            <li key={finding}>{finding}</li>
          ))}
        </ul>
      )}
      {counts.length > 0 && (
        <div className="crash-counts">
          {counts.map(([kind, count]) => (
            <span key={kind}>
              {countLabel(kind)} · {count}
            </span>
          ))}
        </div>
      )}
      {analysis.timeline.length > 0 && (
        <details className="crash-timeline">
          <summary>Relevant retained log timeline</summary>
          <div>
            {analysis.timeline.map((entry) => (
              <article key={`${entry.timestamp}:${entry.summary}`}>
                <time>{formatRelativeTime(entry.timestamp)}</time>
                <strong>{entry.summary}</strong>
                {entry.message && <p>{entry.message}</p>}
              </article>
            ))}
          </div>
        </details>
      )}
    </>
  );
}

function CrashHistoryCard({ item }: { item: CrashHistoryItem }) {
  return (
    <details className={`crash-history-card crash-history-card--${item.level}`}>
      <summary>
        <span>{item.level}</span>
        <span>
          <strong>{item.headline}</strong>
          <small>
            Previous boot{' '}
            {item.previousBootEndedAt ? formatRelativeTime(item.previousBootEndedAt) : 'unknown'}
            {' · '}analyzed {formatRelativeTime(item.analyzedAt)}
          </small>
        </span>
      </summary>
      <CrashDetails analysis={item} />
    </details>
  );
}

export function SystemHealthSheet({
  open,
  onClose,
  rangeHours,
  onRangeChange,
  resource,
  crashHistory,
  crashAnalysis,
}: SystemHealthSheetProps) {
  const report = resource.data;
  const current = report?.current;
  const awaitingSelectedRange = report !== null && report.rangeHours !== rangeHours;
  const rangeStatus = awaitingSelectedRange
    ? `Loading ${systemHealthRangeLabel(rangeHours)}…`
    : report
      ? `${systemHealthRangeLabel(report.rangeHours)} · ${report.eventCount} events`
      : resource.initialLoading
        ? 'Loading…'
        : 'No report';

  const currentItems = [
    { label: 'CPU now', value: formatPercent(current?.cpuPercent, 1) },
    { label: 'Memory now', value: formatPercent(current?.memoryPercent, 1) },
    { label: 'CPU / SoC temperature', value: temperature(current?.temperatureCelsius) },
    {
      label: 'Arm clock',
      value:
        current?.armMegahertz === null || current?.armMegahertz === undefined
          ? '—'
          : `${current.armMegahertz.toFixed(0)} MHz`,
    },
    { label: 'Firmware word', value: current?.throttleWord ?? '—' },
    {
      label: 'Active flags',
      value: current?.activeThrottleFlags.length ? current.activeThrottleFlags.join(', ') : 'none',
    },
    { label: 'Network receive', value: rate(current?.networkReceiveBytesPerSecond) },
    { label: 'Network transmit', value: rate(current?.networkTransmitBytesPerSecond) },
    { label: 'Disk read', value: rate(current?.diskReadBytesPerSecond) },
    { label: 'Disk write', value: rate(current?.diskWriteBytesPerSecond) },
  ];
  const peakItems = [
    { label: 'CPU', value: formatPercent(report?.peaks.cpuPercent.value, 1) },
    { label: 'Memory', value: formatPercent(report?.peaks.memoryPercent.value, 1) },
    { label: 'Temperature', value: temperature(report?.peaks.temperatureCelsius.value) },
    { label: 'Network receive', value: rate(report?.peaks.networkReceiveBytesPerSecond.value) },
    { label: 'Network transmit', value: rate(report?.peaks.networkTransmitBytesPerSecond.value) },
    { label: 'Disk read', value: rate(report?.peaks.diskReadBytesPerSecond.value) },
    { label: 'Disk write', value: rate(report?.peaks.diskWriteBytesPerSecond.value) },
    { label: 'Disk busy', value: formatPercent(report?.peaks.diskBusyPercent.value, 1) },
  ];

  return (
    <BottomSheet
      open={open}
      title="System Health"
      description="Passive Pi power, throttling, storage, and resource evidence."
      onClose={onClose}
    >
      <div className="system-health-sheet__toolbar">
        <HealthRangeSelector
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

      <div className="system-health-sheet__stack" aria-busy={resource.refreshing}>
        <section
          className={`system-health-diagnosis system-health-diagnosis--${report?.level ?? 'unknown'}`}
        >
          <span>{report?.level ?? 'No data'}</span>
          <h3>{report?.headline ?? 'Waiting for monitor data…'}</h3>
          {report?.findings.length ? (
            <ul>
              {report.findings.map((finding) => (
                <li key={finding}>{finding}</li>
              ))}
            </ul>
          ) : null}
        </section>

        <div className="system-health-sheet__columns">
          <section className="panel-card">
            <h3>Current system state</h3>
            <KeyValueList items={currentItems} />
          </section>
          <section className="panel-card">
            <h3>Resource peaks</h3>
            <KeyValueList items={peakItems} />
          </section>
        </div>

        <section className="panel-card">
          <h3>Resource-hungry processes</h3>
          <div className="system-health-processes">
            {report?.repeatOffenders.length ? (
              report.repeatOffenders.map((process) => (
                <article key={process.name}>
                  <span>
                    <strong>{process.name}</strong>
                    <small>
                      {process.peakCount} peak lead{process.peakCount === 1 ? '' : 's'} ·{' '}
                      {process.cpuPeakCount} CPU · {process.memoryPeakCount} memory
                    </small>
                  </span>
                  <span>
                    {process.maximumCpuPercent === null
                      ? '—'
                      : `${process.maximumCpuPercent.toFixed(1)}% CPU`}
                    <small>{formatBytes(process.maximumResidentBytes)} RSS</small>
                  </span>
                </article>
              ))
            ) : (
              <p className="system-health-sheet__empty">No retained process peaks.</p>
            )}
          </div>
        </section>

        <section className="panel-card">
          <h3>Recent event timeline</h3>
          <div className="system-health-events">
            {report?.events.length ? (
              report.events.map((event) => (
                <article
                  className={`system-health-event system-health-event--${event.severity}`}
                  key={`${event.timestamp}:${event.summary}`}
                >
                  <header>
                    <span>{event.category}</span>
                    <time>{formatRelativeTime(event.timestamp)}</time>
                  </header>
                  <strong>{event.summary}</strong>
                  {event.message && <p>{event.message}</p>}
                </article>
              ))
            ) : (
              <p className="system-health-sheet__empty">No events in this range.</p>
            )}
          </div>
        </section>

        <section className="panel-card crash-analysis" aria-labelledby="crash-analysis-title">
          <header>
            <div>
              <h3 id="crash-analysis-title">Previous-boot crash analysis</h3>
              <p>Save bounded preceding-boot evidence and compare it with earlier analyses.</p>
            </div>
            <button
              className="primary-button"
              type="button"
              disabled={crashAnalysis.running}
              onClick={() => void crashAnalysis.analyze()}
            >
              {crashAnalysis.running ? 'Analyzing logs…' : 'Analyze previous crash'}
            </button>
          </header>
          {crashAnalysis.error && <p className="error-message">{crashAnalysis.error}</p>}
          {crashAnalysis.result && (
            <article
              className={`crash-analysis-result crash-analysis-result--${crashAnalysis.result.analysis.level}`}
            >
              <span>{crashAnalysis.result.analysis.level}</span>
              <h4>{crashAnalysis.result.analysis.headline}</h4>
              <CrashDetails analysis={crashAnalysis.result.analysis} />
              {crashAnalysis.result.comparison && (
                <p className="crash-comparison">
                  Compared with {crashAnalysis.result.comparison.previousHeadline} (
                  {crashAnalysis.result.comparison.previousLevel})
                </p>
              )}
            </article>
          )}
          <div className="crash-history-heading">
            <h4>Saved analyses</h4>
            <button
              className="secondary-button"
              type="button"
              disabled={crashHistory.refreshing || crashAnalysis.running}
              onClick={() => void crashHistory.refresh()}
            >
              {crashHistory.refreshing ? 'Refreshing…' : 'Refresh history'}
            </button>
          </div>
          {crashHistory.error && <p className="error-message">{crashHistory.error.message}</p>}
          <div className="crash-history-list" aria-busy={crashHistory.refreshing}>
            {crashHistory.data?.items.length ? (
              crashHistory.data.items.map((item) => <CrashHistoryCard item={item} key={item.id} />)
            ) : (
              <p className="system-health-sheet__empty">
                {crashHistory.initialLoading ? 'Loading saved analyses…' : 'No saved analyses yet.'}
              </p>
            )}
          </div>
        </section>
      </div>
    </BottomSheet>
  );
}
