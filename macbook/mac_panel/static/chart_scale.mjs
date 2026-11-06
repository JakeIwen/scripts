// Pure scale maths shared by every chart (tested under Node). Times are epoch seconds.

export const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
export const round = value => Math.round(value * 10) / 10;

export const timeToX = (ts, {from, to, width}) => clamp((ts - from) / (to - from), 0, 1) * width;
export const xToTime = (x, {from, to, width}) => from + clamp(x / width, 0, 1) * (to - from);
