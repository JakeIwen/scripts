import { clockLabel, dayLabel, niceMax } from '../../components/charts/axisLabels';
import { ChartAxes } from '../../components/charts/ChartAxes';
import {
  CHART_HEIGHT,
  CHART_MARGIN,
  makeFrame,
  type Frame,
} from '../../components/charts/chartFrame';
import {
  SeriesMarks,
  type ChartPoint,
  type ChartSeries,
} from '../../components/charts/SeriesMarks';
import { TimeSeriesChart } from '../../components/charts/TimeSeriesChart';
import { useElementWidth } from '../../components/charts/useElementWidth';
import { formatSpeed } from '../../utils/format';
import {
  formatMegabits,
  linearTrend,
  rollingMean,
  ROLLING_SAMPLES,
  RUN_GAP_SECONDS,
  runAverages,
  speedStats,
  trendAt,
  type LinearTrend,
  type SpeedRun,
  type SpeedSample,
} from './uploadSpeeds';

const MIN_BAR_WIDTH = 4;
const MIN_HIT_WIDTH = 14;

function chartPoints(samples: SpeedSample[]): ChartPoint[] {
  return samples.map(({ at, speed }) => ({ at, value: speed }));
}

function trendSeries(trend: LinearTrend, start: number, end: number): ChartSeries {
  return {
    label: 'Trend',
    color: 'var(--warning)',
    variant: 'dashed',
    points: [start, end].map((at) => ({ at, value: trendAt(trend, at) })),
  };
}

function RunBar({
  frame,
  run,
  average,
  selected,
  onToggle,
}: {
  frame: Frame;
  run: SpeedRun;
  average: number;
  selected: boolean;
  onToggle: () => void;
}) {
  const left = frame.x(run.start);
  const barWidth = Math.max(MIN_BAR_WIDTH, frame.x(run.end) - left);
  const hitWidth = Math.max(MIN_HIT_WIDTH, barWidth);
  const top = frame.y(average);
  const label = `${dayLabel(run.start)} ${clockLabel(run.start)} · ${formatSpeed(average)} average`;
  return (
    <g
      className={`upload-chart__run${selected ? ' upload-chart__run--selected' : ''}`}
      role="button"
      tabIndex={0}
      aria-label={label}
      aria-pressed={selected}
      onClick={onToggle}
      onKeyDown={(event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        onToggle();
      }}
    >
      <title>{label}</title>
      {/* A wider transparent target keeps one-minute runs tappable. */}
      <rect
        className="upload-chart__hit"
        x={left + barWidth / 2 - hitWidth / 2}
        y={CHART_MARGIN.top}
        width={hitWidth}
        height={frame.plotHeight}
      />
      <rect
        className="upload-chart__bar"
        x={left}
        y={top}
        width={barWidth}
        height={Math.max(2, frame.y(0) - top)}
        rx={2}
      />
    </g>
  );
}

/** Every run in the 30-day window as a bar at its mean speed; selecting one focuses it. */
export function RunOverviewChart({
  runs,
  start,
  end,
  selected,
  onSelect,
}: {
  runs: SpeedRun[];
  start: number;
  end: number;
  selected: number | null;
  onSelect: (index: number | null) => void;
}) {
  const [ref, width] = useElementWidth();
  const averages = runAverages(runs);
  const peak = Math.max(0, ...averages.map((point) => point.speed));
  const frame = makeFrame(width, start, end, 0, niceMax(peak));
  const trend = linearTrend(averages);
  return (
    <div ref={ref} className="time-chart">
      <svg
        width={width}
        height={CHART_HEIGHT}
        role="img"
        aria-label="Average upload speed of each run over 30 days"
      >
        <ChartAxes frame={frame} formatValue={formatSpeed} />
        {trend && (
          <SeriesMarks
            frame={frame}
            series={[trendSeries(trend, start, end)]}
            gapSeconds={Infinity}
          />
        )}
        {runs.map((run, index) => (
          <RunBar
            key={run.start}
            frame={frame}
            run={run}
            average={averages[index]?.speed ?? 0}
            selected={selected === index}
            onToggle={() => onSelect(selected === index ? null : index)}
          />
        ))}
      </svg>
    </div>
  );
}

/** Minute-by-minute speed for one run, its rolling mean and its least-squares trend. */
export function RunDetailChart({ run }: { run: SpeedRun }) {
  const { samples } = run;
  const trend = linearTrend(samples);
  const series: ChartSeries[] = [
    {
      label: 'Per minute',
      color: 'var(--accent)',
      variant: 'faint',
      points: chartPoints(samples),
    },
    {
      label: `${ROLLING_SAMPLES}-minute average`,
      color: 'var(--accent)',
      points: chartPoints(rollingMean(samples)),
    },
  ];
  return (
    <TimeSeriesChart
      ariaLabel="Upload speed during the selected run"
      series={series}
      guides={trend ? [trendSeries(trend, run.start, run.end)] : []}
      start={run.start}
      end={run.end}
      yMax={niceMax(speedStats(samples)?.peak ?? 0)}
      formatValue={formatSpeed}
      formatReadout={(speed) => `${formatSpeed(speed)} · ${formatMegabits(speed)}`}
      // Runs end at a longer pause, so the line never breaks inside one.
      gapSeconds={RUN_GAP_SECONDS}
    />
  );
}
