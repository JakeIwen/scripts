import { useCallback, useState } from 'react';
import { BottomSheet } from '../../components/BottomSheet';
import { niceMax } from '../../components/charts/axisLabels';
import { TimeSeriesChart } from '../../components/charts/TimeSeriesChart';
import { usePollingResource } from '../../hooks/usePollingResource';
import {
  formatBytes,
  formatDuration,
  formatPercent,
  formatRelativeTime,
  formatSpeed,
} from '../../utils/format';
import { fetchDiskHistory } from './api';
import {
  DEFAULT_HISTORY_HOURS,
  DISK_HISTORY_REFRESH_MS,
  HISTORY_RANGES,
  historyHours,
  historySeries,
  historySummary,
  SMART_INTERVAL_SECONDS,
  TEMPERATURE_LIMIT_C,
  temperatureAxis,
  type HistoryHours,
  type HistorySummary,
} from './diskStats';
import type { DiskHistory, SmartReading } from './types';

function celsius(value: number): string {
  return `${value.toFixed(0)} °C`;
}

function count(value: number | null): string {
  return value === null ? 'not reported' : value.toLocaleString();
}

/** One disk's I/O, busy time and temperature over a chosen range, plus its last SMART reading. */
export function DiskStatsSheet({ label, onClose }: { label: string; onClose: () => void }) {
  const [hours, setHours] = useState<HistoryHours>(DEFAULT_HISTORY_HOURS);
  // A new loader makes the hook abort the old range's request and poll the new one.
  const load = useCallback(
    (signal: AbortSignal) => fetchDiskHistory(label, hours, signal),
    [label, hours],
  );
  const resource = usePollingResource({ load, intervalMs: DISK_HISTORY_REFRESH_MS });
  const history = resource.data && historyHours(resource.data) === hours ? resource.data : null;
  return (
    <BottomSheet
      open
      title={`${label} history`}
      description="I/O each minute from the system monitor; SMART every 10 minutes while mounted."
      className="disk-history-sheet"
      onClose={onClose}
    >
      <div className="disk-history__ranges" role="group" aria-label="History range">
        {HISTORY_RANGES.map((range) => (
          <button
            key={range.hours}
            type="button"
            className="disk-history__range"
            aria-pressed={range.hours === hours}
            onClick={() => setHours(range.hours)}
          >
            {range.label}
          </button>
        ))}
      </div>
      {resource.error && !history ? (
        <div role="alert">
          <p>{resource.error.message}</p>
          <button type="button" className="button" onClick={() => void resource.refresh()}>
            Retry
          </button>
        </div>
      ) : history === null ? (
        <p role="status">Loading disk history…</p>
      ) : (
        <HistoryBody history={history} />
      )}
    </BottomSheet>
  );
}

function HistoryBody({ history }: { history: DiskHistory }) {
  const summary = historySummary(history);
  const series = historySeries(history);
  const ioGap = history.bucketSeconds + 1;
  const peakRate = Math.max(
    0,
    ...series.read.map((p) => p.value),
    ...series.write.map((p) => p.value),
  );
  return (
    <>
      {summary ? (
        <SummaryGrid summary={summary} />
      ) : (
        <p className="disk-history__empty">Nothing was recorded for this disk in this range.</p>
      )}
      <h3 className="disk-history__heading">Read and write</h3>
      {series.read.length ? (
        <TimeSeriesChart
          ariaLabel="Read and write rate over time"
          series={[
            { label: 'Read', color: 'var(--accent)', points: series.read },
            { label: 'Write', color: 'var(--warning)', points: series.write },
          ]}
          start={history.since}
          end={history.until}
          yMax={niceMax(peakRate * 1.05)}
          formatValue={formatSpeed}
          gapSeconds={ioGap}
        />
      ) : (
        <p className="disk-history__empty">
          No I/O recorded. The system monitor logs each attached disk once a minute.
        </p>
      )}
      {series.busy.length > 0 && (
        <>
          <h3 className="disk-history__heading">Busy</h3>
          <TimeSeriesChart
            ariaLabel="Share of time with I/O queued"
            series={[{ label: 'Busy', color: 'var(--good)', points: series.busy }]}
            start={history.since}
            end={history.until}
            yMax={100}
            formatValue={(value) => formatPercent(value)}
            gapSeconds={ioGap}
          />
        </>
      )}
      <h3 className="disk-history__heading">Temperature</h3>
      {series.temperature.length && summary?.temperature ? (
        <TimeSeriesChart
          ariaLabel="Drive temperature over time"
          series={[{ label: 'Temperature', color: 'var(--bad)', points: series.temperature }]}
          start={history.since}
          end={history.until}
          {...temperatureAxis(summary.temperature)}
          formatValue={celsius}
          // Missing one 10-minute reading still joins the line; an unmount does not.
          gapSeconds={Math.max(history.bucketSeconds, SMART_INTERVAL_SECONDS) * 2}
          limit={{ value: TEMPERATURE_LIMIT_C, label: `${TEMPERATURE_LIMIT_C} °C alert` }}
        />
      ) : (
        <p className="disk-history__empty">
          No temperature in this range. SMART is read only from mounted HDDs, so parked disks are
          never woken; flash drives report none.
        </p>
      )}
      {history.smart && <SmartDetails smart={history.smart} />}
    </>
  );
}

function SummaryGrid({ summary }: { summary: HistorySummary }) {
  const rows: [string, string][] = [
    ['Average read', formatSpeed(summary.averageRead)],
    ['Average write', formatSpeed(summary.averageWrite)],
    ['Peak read minute', formatSpeed(summary.peakRead)],
    ['Peak write minute', formatSpeed(summary.peakWrite)],
    ['Read', `≈ ${formatBytes(summary.readBytes)}`],
    ['Written', `≈ ${formatBytes(summary.writeBytes)}`],
    ['Average busy', formatPercent(summary.averageBusy, 1)],
    ['Peak busy minute', formatPercent(summary.peakBusy, 1)],
    ['Recorded', formatDuration(summary.recordedSeconds)],
  ];
  if (summary.temperature)
    rows.push(
      ['Coolest', celsius(summary.temperature.low)],
      ['Average temp', celsius(summary.temperature.average)],
      ['Hottest', celsius(summary.temperature.high)],
    );
  return <StatList rows={rows} />;
}

function SmartDetails({ smart }: { smart: SmartReading }) {
  return (
    <>
      <h3 className="disk-history__heading">Last SMART reading · {formatRelativeTime(smart.at)}</h3>
      <StatList
        rows={[
          ['Health', smart.passed === null ? 'unknown' : smart.passed ? 'Passed' : 'FAILED'],
          [
            'Temperature',
            smart.temperatureC === null ? 'not reported' : celsius(smart.temperatureC),
          ],
          ['Reallocated sectors', count(smart.reallocatedSectors)],
          ['Pending sectors', count(smart.pendingSectors)],
          ['Uncorrectable sectors', count(smart.uncorrectableSectors)],
          [
            'Powered on',
            smart.powerOnHours === null ? 'not reported' : `${count(smart.powerOnHours)} h`,
          ],
          ['Power cycles', count(smart.powerCycles)],
        ]}
      />
    </>
  );
}

function StatList({ rows }: { rows: [string, string][] }) {
  return (
    <dl className="disk-history__stats">
      {rows.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}
