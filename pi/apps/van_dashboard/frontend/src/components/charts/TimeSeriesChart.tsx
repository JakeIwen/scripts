import { useState } from 'react';
import { ChartAxes, HorizontalLine } from './ChartAxes';
import { CHART_HEIGHT, CHART_MARGIN, makeFrame, type Frame } from './chartFrame';
import { SeriesMarks, type ChartPoint, type ChartSeries } from './SeriesMarks';
import { useElementWidth } from './useElementWidth';
import './timeChart.css';

interface Readout {
  series: ChartSeries;
  point: ChartPoint | undefined;
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

function Crosshair({ frame, at, readouts }: { frame: Frame; at: number; readouts: Readout[] }) {
  return (
    <g className="time-chart__crosshair" aria-hidden="true">
      <line
        x1={frame.x(at)}
        x2={frame.x(at)}
        y1={CHART_MARGIN.top}
        y2={CHART_MARGIN.top + frame.plotHeight}
      />
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
  const x = frame.x(at);
  return (
    <div
      className="time-chart__tooltip"
      role="status"
      // Slide from left-aligned at the chart's left edge to right-aligned at its
      // right edge, so a wide readout never hangs outside the chart.
      style={{ left: x, transform: `translateX(-${(x / frame.width) * 100}%)` }}
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

function ChartLegend({ series }: { series: ChartSeries[] }) {
  return (
    <ul className="time-chart__legend">
      {series.map((item) => (
        <li
          key={item.label}
          className={item.variant && `time-chart__key--${item.variant}`}
          style={{ '--series-color': item.color } as React.CSSProperties}
        >
          {item.label}
        </li>
      ))}
    </ul>
  );
}

/**
 * Lines over a fixed time window with gaps where data stops, a shared hover
 * crosshair and an optional dashed limit. Pointer and touch both pick a time.
 * Guides, such as a fitted trend, are drawn unbroken and never read on hover.
 */
export function TimeSeriesChart({
  ariaLabel,
  series,
  guides = [],
  start,
  end,
  yMin = 0,
  yMax,
  formatValue,
  formatReadout = formatValue,
  gapSeconds,
  limit,
}: {
  ariaLabel: string;
  series: ChartSeries[];
  guides?: ChartSeries[];
  start: number;
  end: number;
  yMin?: number;
  yMax: number;
  formatValue: (value: number) => string;
  /** The hover readout's value text, which has room for more than an axis label. */
  formatReadout?: (value: number) => string;
  gapSeconds: number;
  limit?: { value: number; label: string };
}) {
  const [ref, width] = useElementWidth();
  const [hoverAt, setHoverAt] = useState<number | null>(null);
  const frame = makeFrame(width, start, end, yMin, yMax);
  const hover = hoverAt === null ? null : readHover(series, hoverAt, gapSeconds);
  const legend = [...series, ...guides];
  const pick = (event: React.PointerEvent<SVGRectElement>) => {
    const bounds = event.currentTarget.ownerSVGElement?.getBoundingClientRect();
    if (!bounds) return;
    const fraction = (event.clientX - bounds.left - CHART_MARGIN.left) / frame.plotWidth;
    setHoverAt(start + Math.min(1, Math.max(0, fraction)) * frame.span);
  };
  return (
    <div ref={ref} className="time-chart">
      {legend.length > 1 && <ChartLegend series={legend} />}
      <svg width={width} height={CHART_HEIGHT} role="img" aria-label={ariaLabel}>
        <ChartAxes frame={frame} formatValue={formatValue} />
        {limit && limit.value <= yMax && (
          <g className="time-chart__limit">
            <HorizontalLine frame={frame} value={limit.value} />
            <text x={width - CHART_MARGIN.right} y={frame.y(limit.value) - 4} textAnchor="end">
              {limit.label}
            </text>
          </g>
        )}
        <SeriesMarks frame={frame} series={series} gapSeconds={gapSeconds} />
        <SeriesMarks frame={frame} series={guides} gapSeconds={Infinity} />
        {hover && <Crosshair frame={frame} at={hover.at} readouts={hover.readouts} />}
        <rect
          className="time-chart__overlay"
          x={CHART_MARGIN.left}
          y={CHART_MARGIN.top}
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
          formatValue={formatReadout}
        />
      )}
    </div>
  );
}
