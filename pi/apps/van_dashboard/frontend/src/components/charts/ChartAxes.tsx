import { clockLabel, dayLabel } from './axisLabels';
import { CHART_HEIGHT, CHART_MARGIN, type Frame } from './chartFrame';
import './timeChart.css';

// Past a day and a half the x labels need the date rather than the clock.
const DAY_LABEL_SPAN_SECONDS = 36 * 3600;

export function HorizontalLine({ frame, value }: { frame: Frame; value: number }) {
  return (
    <line
      x1={CHART_MARGIN.left}
      x2={frame.width - CHART_MARGIN.right}
      y1={frame.y(value)}
      y2={frame.y(value)}
    />
  );
}

/** Gridlines with value labels at the bottom, middle and top, and time labels along the x axis. */
export function ChartAxes({
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
          <text x={CHART_MARGIN.left - 6} y={frame.y(value) + 4} textAnchor="end">
            {formatValue(value)}
          </text>
        </g>
      ))}
      {xTicks.map((at, index) => (
        <text
          key={at}
          x={frame.x(at)}
          y={CHART_HEIGHT - 6}
          textAnchor={index === 0 ? 'start' : index === xTicks.length - 1 ? 'end' : 'middle'}
        >
          {label(at)}
        </text>
      ))}
    </g>
  );
}
