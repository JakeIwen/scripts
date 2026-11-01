export const CHART_HEIGHT = 150;
export const CHART_MARGIN = { top: 10, right: 10, bottom: 24, left: 62 };

/** A chart's measured width and the scales that place a time and a value in its plot. */
export interface Frame {
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

export function makeFrame(
  width: number,
  start: number,
  end: number,
  yMin: number,
  yMax: number,
): Frame {
  const plotWidth = Math.max(1, width - CHART_MARGIN.left - CHART_MARGIN.right);
  const plotHeight = CHART_HEIGHT - CHART_MARGIN.top - CHART_MARGIN.bottom;
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
    x: (at) => CHART_MARGIN.left + ((at - start) / span) * plotWidth,
    y: (value) =>
      CHART_MARGIN.top +
      plotHeight -
      ((Math.min(Math.max(value, yMin), yMax) - yMin) / range) * plotHeight,
  };
}
