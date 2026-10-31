import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { BottomSheet } from '../../components/BottomSheet';
import { TooltipButton } from '../../components/TooltipButton';
import { formatBytes, formatDuration } from '../../utils/format';
import { iCloudPhaseLabel, type ICloudAttempt, type ICloudBackupKind } from './icloud';
import { RunDetailChart, RunOverviewChart } from './UploadSpeedCharts';
import {
  attemptForRun,
  formatMegabits,
  formatSpeed,
  linearTrend,
  loadUploadSpeeds,
  RETENTION_SECONDS,
  runAverages,
  speedStats,
  splitRuns,
  trendAt,
  type SpeedRun,
  type SpeedSample,
  type SpeedStats,
} from './uploadSpeeds';
import './uploadSpeeds.css';

const LIVE_REFRESH_MS = 60_000;

export function UploadSpeedButton({
  kind,
  title,
  attempts,
  running,
}: {
  kind: ICloudBackupKind;
  title: string;
  attempts: ICloudAttempt[];
  running: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        className="upload-speed-button"
        aria-label="Upload speed graph"
        onClick={() => setOpen(true)}
      >
        <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">
          <path d="M2 17h16" />
          <path d="M3 14l4-5 3 3 4-6 3 3" />
        </svg>
      </button>
      {/* The opener sits inside the card's description paragraph, which cannot hold a dialog. */}
      {open &&
        createPortal(
          <UploadSpeedSheet
            kind={kind}
            title={title}
            attempts={attempts}
            running={running}
            onClose={() => setOpen(false)}
          />,
          document.body,
        )}
    </>
  );
}

/** Focus is a run's start time, 'all' for the 30-day summary, or null for the latest run. */
type Focus = number | 'all' | null;

function UploadSpeedSheet({
  kind,
  title,
  attempts,
  running,
  onClose,
}: {
  kind: ICloudBackupKind;
  title: string;
  attempts: ICloudAttempt[];
  running: boolean;
  onClose: () => void;
}) {
  const [samples, setSamples] = useState<SpeedSample[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [focus, setFocus] = useState<Focus>(null);
  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    loadUploadSpeeds(kind, controller.signal).then(
      (value) => {
        if (!controller.signal.aborted) setSamples(value);
      },
      (failure: unknown) => {
        if (!controller.signal.aborted)
          setError(failure instanceof Error ? failure.message : 'Could not load upload speeds.');
      },
    );
    const timer = running
      ? window.setTimeout(() => setReload((value) => value + 1), LIVE_REFRESH_MS)
      : null;
    return () => {
      controller.abort();
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [kind, reload, running]);
  return (
    <BottomSheet
      open
      title="Upload speeds"
      description={`${title} · last 30 days`}
      className="upload-speed-sheet"
      onClose={onClose}
    >
      {error && !samples ? (
        <div role="alert">
          <p>{error}</p>
          <button type="button" className="button" onClick={() => setReload((value) => value + 1)}>
            Retry
          </button>
        </div>
      ) : samples === null ? (
        <p role="status">Loading upload speeds…</p>
      ) : samples.length === 0 ? (
        <p>
          No uploads in the last 30 days. The Pi records one average speed per minute while an
          upload runs and drops samples after 30 days.
        </p>
      ) : (
        <SpeedHistory samples={samples} attempts={attempts} focus={focus} onFocus={setFocus} />
      )}
    </BottomSheet>
  );
}

function SpeedHistory({
  samples,
  attempts,
  focus,
  onFocus,
}: {
  samples: SpeedSample[];
  attempts: ICloudAttempt[];
  focus: Focus;
  onFocus: (focus: Focus) => void;
}) {
  const now = Date.now() / 1000;
  const runs = splitRuns(samples);
  const index =
    focus === 'all'
      ? null
      : focus === null
        ? runs.length - 1
        : runs.findIndex((run) => run.start === focus);
  const run = index === null || index < 0 ? null : (runs[index] ?? null);
  const overall = speedStats(samples);
  return (
    <>
      <div className="upload-speed__scope" role="group" aria-label="Statistics scope">
        <button
          type="button"
          className="upload-speed__scope-button"
          aria-pressed={run === null}
          onClick={() => onFocus('all')}
        >
          All 30 days
        </button>
        <button
          type="button"
          className="upload-speed__scope-button"
          aria-pressed={run !== null && index === runs.length - 1}
          onClick={() => onFocus(null)}
        >
          Latest run
        </button>
      </div>
      {run ? (
        <RunSummary run={run} overall={overall} attempts={attempts} now={now} />
      ) : (
        <PeriodSummary runs={runs} stats={overall} />
      )}
      <h3 className="upload-speed__heading">Runs · average speed</h3>
      <RunOverviewChart
        runs={runs}
        start={now - RETENTION_SECONDS}
        end={now}
        selected={run ? index : null}
        onSelect={(selected) =>
          onFocus(selected === null ? 'all' : (runs[selected]?.start ?? 'all'))
        }
      />
      {run && (
        <>
          <h3 className="upload-speed__heading">Selected run · minute by minute</h3>
          <RunDetailChart run={run} />
        </>
      )}
      <RunTable
        runs={runs}
        attempts={attempts}
        now={now}
        selected={run}
        onSelect={(selected) => onFocus(selected.start)}
      />
    </>
  );
}

function percent(value: number): string {
  return `${value >= 0 ? '+' : ''}${value.toFixed(0)}%`;
}

/** One stat box: label, value and an optional tap/hover explanation. */
type StatRow = [label: string, value: string, hint?: string];

function StatGrid({ stats, extra }: { stats: SpeedStats; extra: StatRow[] }) {
  const rows: StatRow[] = [
    ['Average', formatSpeed(stats.average), formatMegabits(stats.average)],
    ['Median', formatSpeed(stats.median), 'Half of all minutes were faster than this.'],
    ['Peak minute', formatSpeed(stats.peak), formatMegabits(stats.peak)],
    ['Uploading', formatDuration(stats.uploadingSeconds), 'Minutes with a recorded sample.'],
    [
      'Sent',
      `≈ ${formatBytes(stats.sentBytes)}`,
      'Estimated from the per-minute averages; includes retried bytes.',
    ],
    ...extra,
  ];
  return (
    <dl className="upload-speed__stats">
      {rows.map(([label, value, hint]) => (
        <div key={label}>
          <dt>
            {label}
            {hint && (
              <TooltipButton
                type="button"
                className="upload-speed__info"
                aria-label={`About ${label}`}
                tooltip={hint}
              >
                i
              </TooltipButton>
            )}
          </dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function PeriodSummary({ runs, stats }: { runs: SpeedRun[]; stats: SpeedStats | null }) {
  if (!stats) return null;
  const trend = linearTrend(runAverages(runs));
  const extra: StatRow[] = [['Runs', String(runs.length)]];
  if (trend)
    extra.push([
      'Trend',
      `${percent(((trend.slope * 7 * 86_400) / stats.average) * 100)} / week`,
      'Least-squares fit through each run’s average speed.',
    ]);
  return <StatGrid stats={stats} extra={extra} />;
}

function RunSummary({
  run,
  overall,
  attempts,
  now,
}: {
  run: SpeedRun;
  overall: SpeedStats | null;
  attempts: ICloudAttempt[];
  now: number;
}) {
  const stats = speedStats(run.samples);
  if (!stats) return null;
  const trend = linearTrend(run.samples);
  const attempt = attemptForRun(run, attempts, now);
  const extra: StatRow[] = [['Started', new Date(run.start * 1000).toLocaleString()]];
  if (overall && overall.average > 0)
    extra.push([
      'Vs 30 days',
      percent((stats.average / overall.average - 1) * 100),
      'This run’s average compared with the 30-day average.',
    ]);
  if (trend && stats.average > 0)
    extra.push([
      'Trend',
      `${percent(((trendAt(trend, run.end) - trendAt(trend, run.start)) / stats.average) * 100)} over run`,
      'Change in the least-squares fit from the run’s first minute to its last.',
    ]);
  if (attempt) extra.push(['Attempt result', iCloudPhaseLabel(attempt.phase)]);
  return <StatGrid stats={stats} extra={extra} />;
}

function RunTable({
  runs,
  attempts,
  now,
  selected,
  onSelect,
}: {
  runs: SpeedRun[];
  attempts: ICloudAttempt[];
  now: number;
  selected: SpeedRun | null;
  onSelect: (run: SpeedRun) => void;
}) {
  return (
    <details className="upload-speed__runs" open={runs.length <= 6}>
      <summary>Runs ({runs.length})</summary>
      <table>
        <thead>
          <tr>
            <th scope="col">Started</th>
            <th scope="col">Length</th>
            <th scope="col">Average</th>
            <th scope="col">Peak</th>
            <th scope="col">Result</th>
          </tr>
        </thead>
        <tbody>
          {[...runs].reverse().map((run) => {
            const stats = speedStats(run.samples);
            const attempt = attemptForRun(run, attempts, now);
            return (
              <tr key={run.start} aria-selected={selected === run}>
                <td>
                  <button
                    type="button"
                    className="upload-speed__run-link"
                    onClick={() => onSelect(run)}
                  >
                    {new Date(run.start * 1000).toLocaleString([], {
                      month: 'short',
                      day: 'numeric',
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                  </button>
                </td>
                <td>{formatDuration(run.end - run.start)}</td>
                <td>{stats ? formatSpeed(stats.average) : '—'}</td>
                <td>{stats ? formatSpeed(stats.peak) : '—'}</td>
                <td>{attempt ? iCloudPhaseLabel(attempt.phase) : '—'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </details>
  );
}
