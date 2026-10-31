import { useState } from 'react';
import { clockLabel, dayLabel } from './axisLabels';
import { useElementWidth } from './useElementWidth';
import './timeSeriesChart.css';

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

interface Readout {
  series: ChartSeries;
  point: ChartPoint | undefined;
}

const HEIGHT = 150;
const MARGIN = { top: 10, right: 10, bottom: 24, left: 62 };
// Past a day and a half the x labels need the date rather than the clock.
const DAY_LABEL_SPAN_SECONDS = 36 * 3600;

interface Frame {
  width: number;
  start: number;
  span: number;
  plotWidth: number;
  plotHeight: number;
  yMin: number;
  yMax: number;
  x: (at: number) => number;
  y: (value: number) => number;
}

function makeFrame(width: number, start: number, end: number, yMin: number, yMax: number): Frame {
  const plotWidth = Math.max(1, width - MARGIN.left - MARGIN.right);
  const plotHeight = HEIGHT - MARGIN.top - MARGIN.bottom;
  const span = Math.max(1, end - start);
  const range = Math.max(1e-9, yMax - yMin);
  return {
    width,
    start,
    span,
    plotWidth,
    plotHeight,
    yMin,
    yMax,
    x: (at) => MARGIN.left + ((at - start) / span) * plotWidth,
    y: (value) =>
      MARGIN.top +
      plotHeight -
      ((Math.min(Math.max(value, yMin), yMax) - yMin) / range) * plotHeight,
  };
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

function nearest(points: ChartPoint[], at: number): ChartPoint | undefined {
  let best: ChartPoint | undefined;
  for (const point of points) {
    if (!best || Math.abs(point.at - at) < Math.abs(best.at - at)) best = point;
  }
  return best;
}

/**
 * Snap the pointer to the nearest point of the densest series, then read every
 * series at that time; a series with nothing within `gapSeconds` reads blank.
 */
function readHover(series: ChartSeries[], hoverAt: number, gapSeconds: number) {
  const densest = series.reduce<ChartSeries | undefined>(
    (best, item) => (!best || item.points.length > best.points.length ? item : best),
    undefined,
  );
  const anchor = densest && nearest(densest.points, hoverAt);
  if (!anchor) return null;
  const readouts: Readout[] = series.map((item) => {
    const point = nearest(item.points, anchor.at);
    return {
      series: item,
      point: point && Math.abs(point.at - anchor.at) <= gapSeconds ? point : undefined,
    };
  });
  return { at: anchor.at, readouts };
}

function HorizontalLine({ frame, value }: { frame: Frame; value: number }) {
  return (
    <line
      x1={MARGIN.left}
      x2={frame.width - MARGIN.right}
      y1={frame.y(value)}
      y2={frame.y(value)}
    />
  );
}

function ChartAxes({
  frame,
  formatValue,
}: {
  frame: Frame;
  formatValue: (value: number) => string;
}) {
  const label = frame.span > DAY_LABEL_SPAN_SECONDS ? dayLabel : clockLabel;
  // Five date labels collide at phone width.
  const tickCount = frame.width < 480 ? 3 : 5;
  const xTicks = Array.from(
    { length: tickCount },
    (_, step) => frame.start + (frame.span * step) / (tickCount - 1),
  );
  return (
    <g className="time-chart__axes" aria-hidden="true">
      {[frame.yMin, (frame.yMin + frame.yMax) / 2, frame.yMax].map((value) => (
        <g key={value}>
          <HorizontalLine frame={frame} value={value} />
          <text x={MARGIN.left - 6} y={frame.y(value) + 4} textAnchor="end">
            {formatValue(value)}
          </text>
        </g>
      ))}
      {xTicks.map((at, index) => (
        <text
          key={at}
          x={frame.x(at)}
          y={HEIGHT - 6}
          textAnchor={index === 0 ? 'start' : index === xTicks.length - 1 ? 'end' : 'middle'}
        >
          {label(at)}
        </text>
      ))}
    </g>
  );
}

function SeriesMarks({
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

function Crosshair({ frame, at, readouts }: { frame: Frame; at: number; readouts: Readout[] }) {
  return (
    <g className="time-chart__crosshair" aria-hidden="true">
      <line x1={frame.x(at)} x2={frame.x(at)} y1={MARGIN.top} y2={MARGIN.top + frame.plotHeight} />
      {readouts.map(
        ({ series, point }) =>
          point && (
            <circle
              key={series.label}
              cx={frame.x(point.at)}
              cy={frame.y(point.value)}
              r={4}
              fill={series.color}
            />
          ),
      )}
    </g>
  );
}

function HoverTooltip({
  frame,
  at,
  readouts,
  formatValue,
}: {
  frame: Frame;
  at: number;
  readouts: Readout[];
  formatValue: (value: number) => string;
}) {
  return (
    <div
      className="time-chart__tooltip"
      role="status"
      style={{ left: Math.min(Math.max(frame.x(at), 80), frame.width - 80) }}
    >
      {new Date(at * 1000).toLocaleString([], {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      })}
      {readouts.map(({ series, point }) => (
        <span key={series.label}>
          <br />
          {readouts.length > 1 && `${series.label} `}
          <strong>{point ? formatValue(point.value) : '—'}</strong>
        </span>
      ))}
    </div>
  );
}

/**
 * Lines over a fixed time window with gaps where data stops, a shared hover
 * crosshair and an optional dashed limit. Pointer and touch both pick a time.
 */
export function TimeSeriesChart({
  ariaLabel,
  series,
  start,
  end,
  yMin = 0,
  yMax,
  formatValue,
  gapSeconds,
  limit,
}: {
  ariaLabel: string;
  series: ChartSeries[];
  start: number;
  end: number;
  yMin?: number;
  yMax: number;
  formatValue: (value: number) => string;
  gapSeconds: number;
  limit?: { value: number; label: string };
}) {
  const [ref, width] = useElementWidth();
  const [hoverAt, setHoverAt] = useState<number | null>(null);
  const frame = makeFrame(width, start, end, yMin, yMax);
  const hover = hoverAt === null ? null : readHover(series, hoverAt, gapSeconds);
  const pick = (event: React.PointerEvent<SVGRectElement>) => {
    const bounds = event.currentTarget.ownerSVGElement?.getBoundingClientRect();
    if (!bounds) return;
    const fraction = (event.clientX - bounds.left - MARGIN.left) / frame.plotWidth;
    setHoverAt(start + Math.min(1, Math.max(0, fraction)) * frame.span);
  };
  return (
    <div ref={ref} className="time-chart">
      {series.length > 1 && (
        <ul className="time-chart__legend">
          {series.map((item) => (
            <li key={item.label} style={{ '--series-color': item.color } as React.CSSProperties}>
              {item.label}
            </li>
          ))}
        </ul>
      )}
      <svg width={width} height={HEIGHT} role="img" aria-label={ariaLabel}>
        <ChartAxes frame={frame} formatValue={formatValue} />
        {limit && limit.value <= yMax && (
          <g className="time-chart__limit">
            <HorizontalLine frame={frame} value={limit.value} />
            <text x={width - MARGIN.right} y={frame.y(limit.value) - 4} textAnchor="end">
              {limit.label}
            </text>
          </g>
        )}
        <SeriesMarks frame={frame} series={series} gapSeconds={gapSeconds} />
        {hover && <Crosshair frame={frame} at={hover.at} readouts={hover.readouts} />}
        <rect
          className="time-chart__overlay"
          x={MARGIN.left}
          y={MARGIN.top}
          width={frame.plotWidth}
          height={frame.plotHeight}
          onPointerMove={pick}
          onPointerDown={pick}
          onPointerLeave={() => setHoverAt(null)}
        />
      </svg>
      {hover && (
        <HoverTooltip
          frame={frame}
          at={hover.at}
          readouts={hover.readouts}
          formatValue={formatValue}
        />
      )}
    </div>
  );
}
