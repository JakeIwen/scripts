import { useState } from 'react';
import { clockLabel, dayLabel, niceMax } from '../../components/charts/axisLabels';
import { useElementWidth } from '../../components/charts/useElementWidth';
import { formatSpeed } from '../../utils/format';
import {
  formatMegabits,
  linearTrend,
  rollingMean,
  ROLLING_SAMPLES,
  runAverages,
  speedStats,
  trendAt,
  type LinearTrend,
  type SpeedRun,
  type SpeedSample,
} from './uploadSpeeds';

const HEIGHT = 170;
const MARGIN = { top: 10, right: 10, bottom: 24, left: 62 };
const MIN_BAR_WIDTH = 4;
const MIN_HIT_WIDTH = 14;

interface Frame {
  width: number;
  x: (at: number) => number;
  y: (speed: number) => number;
  yMax: number;
}

function frame(width: number, start: number, end: number, maxSpeed: number): Frame {
  const span = Math.max(1, end - start);
  const plotWidth = Math.max(1, width - MARGIN.left - MARGIN.right);
  const plotHeight = HEIGHT - MARGIN.top - MARGIN.bottom;
  const yMax = niceMax(maxSpeed * 1.05);
  return {
    width,
    yMax,
    x: (at) => MARGIN.left + ((at - start) / span) * plotWidth,
    y: (speed) => MARGIN.top + plotHeight - (Math.min(speed, yMax) / yMax) * plotHeight,
  };
}

function Axes({ chart, xTicks }: { chart: Frame; xTicks: { at: number; label: string }[] }) {
  const yTicks = [0, chart.yMax / 2, chart.yMax];
  return (
    <g className="upload-chart__axes" aria-hidden="true">
      {yTicks.map((speed) => (
        <g key={speed}>
          <line
            x1={MARGIN.left}
            x2={chart.width - MARGIN.right}
            y1={chart.y(speed)}
            y2={chart.y(speed)}
          />
          <text x={MARGIN.left - 6} y={chart.y(speed) + 4} textAnchor="end">
            {formatSpeed(speed)}
          </text>
        </g>
      ))}
      {xTicks.map((tick, index) => (
        <text
          key={tick.at}
          x={chart.x(tick.at)}
          y={HEIGHT - 6}
          textAnchor={index === 0 ? 'start' : index === xTicks.length - 1 ? 'end' : 'middle'}
        >
          {tick.label}
        </text>
      ))}
    </g>
  );
}

function linePath(points: SpeedSample[], chart: Frame): string {
  return points
    .map(
      (point, index) =>
        `${index ? 'L' : 'M'}${chart.x(point.at).toFixed(1)},${chart.y(point.speed).toFixed(1)}`,
    )
    .join('');
}

function TrendLine({
  chart,
  trend,
  start,
  end,
}: {
  chart: Frame;
  trend: LinearTrend | null;
  start: number;
  end: number;
}) {
  if (!trend) return null;
  return (
    <line
      className="upload-chart__trend"
      x1={chart.x(start)}
      x2={chart.x(end)}
      y1={chart.y(Math.max(0, trendAt(trend, start)))}
      y2={chart.y(Math.max(0, trendAt(trend, end)))}
    />
  );
}

function RunBar({
  chart,
  run,
  average,
  selected,
  onToggle,
}: {
  chart: Frame;
  run: SpeedRun;
  average: number;
  selected: boolean;
  onToggle: () => void;
}) {
  const left = chart.x(run.start);
  const barWidth = Math.max(MIN_BAR_WIDTH, chart.x(run.end) - left);
  const hitWidth = Math.max(MIN_HIT_WIDTH, barWidth);
  const top = chart.y(average);
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
        y={MARGIN.top}
        width={hitWidth}
        height={HEIGHT - MARGIN.top - MARGIN.bottom}
      />
      <rect
        className="upload-chart__bar"
        x={left}
        y={top}
        width={barWidth}
        height={Math.max(2, chart.y(0) - top)}
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
  const chart = frame(width, start, end, Math.max(0, ...averages.map((point) => point.speed)));
  // Five date labels collide at phone width.
  const tickCount = width < 480 ? 3 : 5;
  const xTicks = Array.from({ length: tickCount }, (_, step) => {
    const at = start + ((end - start) * step) / (tickCount - 1);
    return { at, label: dayLabel(at) };
  });
  return (
    <div ref={ref} className="upload-chart">
      <svg
        width={width}
        height={HEIGHT}
        role="img"
        aria-label="Average upload speed of each run over 30 days"
      >
        <Axes chart={chart} xTicks={xTicks} />
        <TrendLine chart={chart} trend={linearTrend(averages)} start={start} end={end} />
        {runs.map((run, index) => (
          <RunBar
            key={run.start}
            chart={chart}
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

function nearestIndex(samples: SpeedSample[], chart: Frame, x: number): number {
  let nearest = 0;
  let distance = Infinity;
  samples.forEach((sample, index) => {
    const gap = Math.abs(chart.x(sample.at) - x);
    if (gap < distance) [nearest, distance] = [index, gap];
  });
  return nearest;
}

function Crosshair({ chart, point }: { chart: Frame; point: SpeedSample }) {
  return (
    <g className="upload-chart__crosshair" aria-hidden="true">
      <line x1={chart.x(point.at)} x2={chart.x(point.at)} y1={MARGIN.top} y2={chart.y(0)} />
      <circle cx={chart.x(point.at)} cy={chart.y(point.speed)} r={4} />
    </g>
  );
}

function PointTooltip({
  chart,
  point,
  smooth,
}: {
  chart: Frame;
  point: SpeedSample;
  smooth: number;
}) {
  return (
    <div
      className="upload-chart__tooltip"
      role="status"
      style={{ left: Math.min(Math.max(chart.x(point.at), 70), chart.width - 70) }}
    >
      <strong>{formatSpeed(point.speed)}</strong> · {formatMegabits(point.speed)}
      <br />
      {clockLabel(point.at)} · {ROLLING_SAMPLES}-min avg {formatSpeed(smooth)}
    </div>
  );
}

/** Minute-by-minute speed for one run, its rolling mean and its least-squares trend. */
export function RunDetailChart({ run }: { run: SpeedRun }) {
  const [ref, width] = useElementWidth();
  const [hover, setHover] = useState<number | null>(null);
  const { samples } = run;
  const smooth = rollingMean(samples);
  const trend = linearTrend(samples);
  const chart = frame(width, run.start, run.end, speedStats(samples)?.peak ?? 0);
  const xTicks = [run.start, (run.start + run.end) / 2, run.end].map((at) => ({
    at,
    label: clockLabel(at),
  }));
  const point = hover === null ? undefined : samples[hover];
  const pick = (event: React.PointerEvent<SVGRectElement>) => {
    const bounds = event.currentTarget.ownerSVGElement?.getBoundingClientRect();
    if (bounds) setHover(nearestIndex(samples, chart, event.clientX - bounds.left));
  };
  return (
    <div ref={ref} className="upload-chart">
      <ul className="upload-chart__legend">
        <li className="upload-chart__key upload-chart__key--raw">Per minute</li>
        <li className="upload-chart__key upload-chart__key--smooth">
          {ROLLING_SAMPLES}-minute average
        </li>
        {trend && <li className="upload-chart__key upload-chart__key--trend">Trend</li>}
      </ul>
      <svg
        width={width}
        height={HEIGHT}
        role="img"
        aria-label="Upload speed during the selected run"
      >
        <Axes chart={chart} xTicks={xTicks} />
        <path className="upload-chart__raw" d={linePath(samples, chart)} />
        <path className="upload-chart__smooth" d={linePath(smooth, chart)} />
        <TrendLine chart={chart} trend={trend} start={run.start} end={run.end} />
        {point && <Crosshair chart={chart} point={point} />}
        <rect
          className="upload-chart__overlay"
          x={MARGIN.left}
          y={MARGIN.top}
          width={Math.max(1, width - MARGIN.left - MARGIN.right)}
          height={HEIGHT - MARGIN.top - MARGIN.bottom}
          onPointerMove={pick}
          onPointerDown={pick}
          onPointerLeave={() => setHover(null)}
        />
      </svg>
      {point && hover !== null && (
        <PointTooltip chart={chart} point={point} smooth={smooth[hover]?.speed ?? point.speed} />
      )}
    </div>
  );
}
