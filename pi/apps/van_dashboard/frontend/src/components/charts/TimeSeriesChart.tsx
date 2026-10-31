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

const HEIGHT = 150;
const MARGIN = { top: 10, right: 10, bottom: 24, left: 62 };
// Past a day and a half the x labels need the date rather than the clock.
const DAY_LABEL_SPAN_SECONDS = 36 * 3600;

interface Frame {
  width: number;
  x: (at: number) => number;
  y: (value: number) => number;
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

function path(points: ChartPoint[], chart: Frame): string {
  return points
    .map(
      (point, index) =>
        `${index ? 'L' : 'M'}${chart.x(point.at).toFixed(1)},${chart.y(point.value).toFixed(1)}`,
    )
    .join('');
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
  const plotWidth = Math.max(1, width - MARGIN.left - MARGIN.right);
  const plotHeight = HEIGHT - MARGIN.top - MARGIN.bottom;
  const span = Math.max(1, end - start);
  const range = Math.max(1e-9, yMax - yMin);
  const chart: Frame = {
    width,
    x: (at) => MARGIN.left + ((at - start) / span) * plotWidth,
    y: (value) =>
      MARGIN.top +
      plotHeight -
      ((Math.min(Math.max(value, yMin), yMax) - yMin) / range) * plotHeight,
  };
  const label = span > DAY_LABEL_SPAN_SECONDS ? dayLabel : clockLabel;
  // Five date labels collide at phone width.
  const tickCount = width < 480 ? 3 : 5;
  const xTicks = Array.from(
    { length: tickCount },
    (_, step) => start + (span * step) / (tickCount - 1),
  );
  const yTicks = [yMin, (yMin + yMax) / 2, yMax];
  const busiest = series.reduce<ChartSeries | undefined>(
    (best, item) => (!best || item.points.length > best.points.length ? item : best),
    undefined,
  );
  const anchor = hoverAt === null || !busiest ? undefined : nearest(busiest.points, hoverAt);
  const picked = anchor
    ? series.map((item) => {
        const point = nearest(item.points, anchor.at);
        return point && Math.abs(point.at - anchor.at) <= gapSeconds ? point : undefined;
      })
    : [];
  const pick = (event: React.PointerEvent<SVGRectElement>) => {
    const bounds = event.currentTarget.ownerSVGElement?.getBoundingClientRect();
    if (!bounds) return;
    const fraction = (event.clientX - bounds.left - MARGIN.left) / plotWidth;
    setHoverAt(start + Math.min(1, Math.max(0, fraction)) * span);
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
        <g className="time-chart__axes" aria-hidden="true">
          {yTicks.map((value) => (
            <g key={value}>
              <line
                x1={MARGIN.left}
                x2={width - MARGIN.right}
                y1={chart.y(value)}
                y2={chart.y(value)}
              />
              <text x={MARGIN.left - 6} y={chart.y(value) + 4} textAnchor="end">
                {formatValue(value)}
              </text>
            </g>
          ))}
          {xTicks.map((at, index) => (
            <text
              key={at}
              x={chart.x(at)}
              y={HEIGHT - 6}
              textAnchor={index === 0 ? 'start' : index === xTicks.length - 1 ? 'end' : 'middle'}
            >
              {label(at)}
            </text>
          ))}
        </g>
        {limit && limit.value <= yMax && (
          <g className="time-chart__limit">
            <line
              x1={MARGIN.left}
              x2={width - MARGIN.right}
              y1={chart.y(limit.value)}
              y2={chart.y(limit.value)}
            />
            <text x={width - MARGIN.right} y={chart.y(limit.value) - 4} textAnchor="end">
              {limit.label}
            </text>
          </g>
        )}
        {series.map((item) =>
          segments(item.points, gapSeconds).map((run) =>
            // A lone reading has no neighbour to draw a line to, so it is a dot.
            run.length === 1 && run[0] ? (
              <circle
                key={`${item.label}-${run[0].at}`}
                className="time-chart__dot"
                cx={chart.x(run[0].at)}
                cy={chart.y(run[0].value)}
                r={2.5}
                fill={item.color}
              />
            ) : (
              <path
                key={`${item.label}-${run[0]?.at}`}
                className="time-chart__line"
                d={path(run, chart)}
                stroke={item.color}
              />
            ),
          ),
        )}
        {anchor && (
          <g className="time-chart__crosshair" aria-hidden="true">
            <line
              x1={chart.x(anchor.at)}
              x2={chart.x(anchor.at)}
              y1={MARGIN.top}
              y2={MARGIN.top + plotHeight}
            />
            {picked.map(
              (point, index) =>
                point && (
                  <circle
                    key={series[index]?.label}
                    cx={chart.x(point.at)}
                    cy={chart.y(point.value)}
                    r={4}
                    fill={series[index]?.color}
                  />
                ),
            )}
          </g>
        )}
        <rect
          className="time-chart__overlay"
          x={MARGIN.left}
          y={MARGIN.top}
          width={plotWidth}
          height={plotHeight}
          onPointerMove={pick}
          onPointerDown={pick}
          onPointerLeave={() => setHoverAt(null)}
        />
      </svg>
      {anchor && (
        <div
          className="time-chart__tooltip"
          role="status"
          style={{ left: Math.min(Math.max(chart.x(anchor.at), 80), width - 80) }}
        >
          {new Date(anchor.at * 1000).toLocaleString([], {
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
          })}
          {series.map((item, index) => (
            <span key={item.label}>
              <br />
              {series.length > 1 && `${item.label} `}
              <strong>{picked[index] ? formatValue(picked[index].value) : '—'}</strong>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
