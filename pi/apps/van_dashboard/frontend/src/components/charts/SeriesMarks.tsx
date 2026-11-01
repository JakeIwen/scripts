import type { Frame } from './chartFrame';
import './timeChart.css';

export interface ChartPoint {
  at: number;
  value: number;
}

/** How a series looks when it isn't a plain line: `faint` puts raw readings behind the series that summarize them. */
export type SeriesVariant = 'faint' | 'dashed';

export interface ChartSeries {
  label: string;
  /** A CSS color, normally a palette token such as `var(--accent)`. */
  color: string;
  points: ChartPoint[];
  variant?: SeriesVariant;
}

/** Split a series wherever consecutive points are further apart than `gapSeconds`. */
export function segments(points: ChartPoint[], gapSeconds: number): ChartPoint[][] {
  const runs: ChartPoint[][] = [];
  for (const point of points) {
    const current = runs.at(-1);
    const previous = current?.at(-1);
    if (current && previous && point.at - previous.at <= gapSeconds) current.push(point);
    else runs.push([point]);
  }
  return runs;
}

function RunMark({ frame, run, color }: { frame: Frame; run: ChartPoint[]; color: string }) {
  const [first] = run;
  if (!first) return null;
  // A lone reading has no neighbour to draw a line to, so it is a dot.
  if (run.length === 1)
    return <circle cx={frame.x(first.at)} cy={frame.y(first.value)} r={2.5} fill={color} />;
  const d = run
    .map(
      (point, index) =>
        `${index ? 'L' : 'M'}${frame.x(point.at).toFixed(1)},${frame.y(point.value).toFixed(1)}`,
    )
    .join('');
  return <path className="time-chart__line" d={d} stroke={color} />;
}

/** Each series as lines broken wherever its data stops for longer than `gapSeconds`. */
export function SeriesMarks({
  frame,
  series,
  gapSeconds,
}: {
  frame: Frame;
  series: ChartSeries[];
  gapSeconds: number;
}) {
  return series.map((item) => (
    <g key={item.label} className={item.variant && `time-chart__series--${item.variant}`}>
      {segments(item.points, gapSeconds).map((run) => (
        <RunMark key={run[0]?.at} frame={frame} run={run} color={item.color} />
      ))}
    </g>
  ));
}
