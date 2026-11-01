import type { Frame } from './chartFrame';
import './timeChart.css';

export interface ChartPoint {
  at: number;
  value: number;
}

export interface ChartSeries {
  label: string;
  /** A CSS color, normally a palette token such as `var(--accent)`. */
  color: string;
  points: ChartPoint[];
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
  return series.flatMap((item) =>
    segments(item.points, gapSeconds).map((run) => {
      const [first] = run;
      if (!first) return null;
      const key = `${item.label}-${first.at}`;
      // A lone reading has no neighbour to draw a line to, so it is a dot.
      if (run.length === 1)
        return (
          <circle
            key={key}
            cx={frame.x(first.at)}
            cy={frame.y(first.value)}
            r={2.5}
            fill={item.color}
          />
        );
      const d = run
        .map(
          (point, index) =>
            `${index ? 'L' : 'M'}${frame.x(point.at).toFixed(1)},${frame.y(point.value).toFixed(1)}`,
        )
        .join('');
      return <path key={key} className="time-chart__line" d={d} stroke={item.color} />;
    }),
  );
}
