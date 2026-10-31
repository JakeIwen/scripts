/** A round axis maximum in the binary units formatBytes prints, close above the data. */
export function niceMax(value: number): number {
  if (value <= 0) return 1024;
  const unit = 1024 ** Math.floor(Math.log(value) / Math.log(1024));
  const scaled = value / unit;
  const magnitude = 10 ** Math.floor(Math.log10(scaled));
  const step = [1, 1.5, 2, 3, 4, 5, 6, 8, 10].find((factor) => factor * magnitude >= scaled) ?? 10;
  return step * magnitude * unit;
}

export function dayLabel(at: number): string {
  return new Date(at * 1000).toLocaleDateString([], { month: 'short', day: 'numeric' });
}

export function clockLabel(at: number): string {
  return new Date(at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}
