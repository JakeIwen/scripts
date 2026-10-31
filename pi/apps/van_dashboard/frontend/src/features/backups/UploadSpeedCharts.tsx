import { useLayoutEffect, useRef, useState } from 'react';
import {
  formatMegabits,
  formatSpeed,
  linearTrend,
  rollingMean,
  ROLLING_SAMPLES,
  runAverages,
  speedStats,
  trendAt,
  type SpeedRun,
  type SpeedSample,
} from './uploadSpeeds';

const HEIGHT = 170;
const MARGIN = { top: 10, right: 10, bottom: 24, left: 62 };
const FALLBACK_WIDTH = 600;
const MIN_BAR_WIDTH = 4;
const MIN_HIT_WIDTH = 14;

function useElementWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(FALLBACK_WIDTH);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    const measure = () => {
      if (element.clientWidth > 0) setWidth(element.clientWidth);
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

/** A round axis maximum in the binary units formatBytes prints, close above the data. */
function niceMax(value: number): number {
  if (value <= 0) return 1024;
  const unit = 1024 ** Math.floor(Math.log(value) / Math.log(1024));
  const scaled = value / unit;
  const magnitude = 10 ** Math.floor(Math.log10(scaled));
  const step = [1, 1.5, 2, 3, 4, 5, 6, 8, 10].find((factor) => factor * magnitude >= scaled) ?? 10;
  return step * magnitude * unit;
}

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

function dayLabel(at: number): string {
  return new Date(at * 1000).toLocaleDateString([], { month: 'short', day: 'numeric' });
}

function clockLabel(at: number): string {
  return new Date(at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
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
  const trend = linearTrend(averages);
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
        {trend && (
          <line
            className="upload-chart__trend"
            x1={chart.x(start)}
            x2={chart.x(end)}
            y1={chart.y(Math.max(0, trendAt(trend, start)))}
            y2={chart.y(Math.max(0, trendAt(trend, end)))}
          />
        )}
        {runs.map((run, index) => {
          const left = chart.x(run.start);
          const barWidth = Math.max(MIN_BAR_WIDTH, chart.x(run.end) - left);
          const hitWidth = Math.max(MIN_HIT_WIDTH, barWidth);
          const average = averages[index]?.speed ?? 0;
          const top = chart.y(average);
          const label = `${dayLabel(run.start)} ${clockLabel(run.start)} · ${formatSpeed(average)} average`;
          return (
            <g
              key={run.start}
              className={`upload-chart__run${selected === index ? ' upload-chart__run--selected' : ''}`}
              role="button"
              tabIndex={0}
              aria-label={label}
              aria-pressed={selected === index}
              onClick={() => onSelect(selected === index ? null : index)}
              onKeyDown={(event) => {
                if (event.key !== 'Enter' && event.key !== ' ') return;
                event.preventDefault();
                onSelect(selected === index ? null : index);
              }}
            >
              <title>{label}</title>
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
        })}
      </svg>
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
  const point = hover === null ? null : samples[hover];
  const pick = (event: React.PointerEvent<SVGRectElement>) => {
    const bounds = event.currentTarget.ownerSVGElement?.getBoundingClientRect();
    if (!bounds) return;
    const x = event.clientX - bounds.left;
    let nearest = 0;
    let distance = Infinity;
    samples.forEach((sample, index) => {
      const gap = Math.abs(chart.x(sample.at) - x);
      if (gap < distance) [nearest, distance] = [index, gap];
    });
    setHover(nearest);
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
        {trend && (
          <line
            className="upload-chart__trend"
            x1={chart.x(run.start)}
            x2={chart.x(run.end)}
            y1={chart.y(Math.max(0, trendAt(trend, run.start)))}
            y2={chart.y(Math.max(0, trendAt(trend, run.end)))}
          />
        )}
        {point && (
          <g className="upload-chart__crosshair" aria-hidden="true">
            <line x1={chart.x(point.at)} x2={chart.x(point.at)} y1={MARGIN.top} y2={chart.y(0)} />
            <circle cx={chart.x(point.at)} cy={chart.y(point.speed)} r={4} />
          </g>
        )}
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
        <div
          className="upload-chart__tooltip"
          role="status"
          style={{
            left: Math.min(Math.max(chart.x(point.at), 70), width - 70),
          }}
        >
          <strong>{formatSpeed(point.speed)}</strong> · {formatMegabits(point.speed)}
          <br />
          {clockLabel(point.at)} · {ROLLING_SAMPLES}-min avg{' '}
          {formatSpeed(smooth[hover]?.speed ?? point.speed)}
        </div>
      )}
    </div>
  );
}
