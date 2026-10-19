// Pure time formatting shared by the page and its Node test; nothing here touches the DOM.

export function parseStamp(value) {
  if (value === null || value === undefined || value === '') return null;
  const date = new Date(typeof value === 'number' ? value * 1000 : String(value).replace(' ', 'T'));
  return Number.isNaN(date.valueOf()) ? null : date.valueOf();
}

export function relativeText(ms, now = Date.now()) {
  const seconds = Math.round((now - ms) / 1000);
  const magnitude = Math.abs(seconds);
  if (magnitude < 45) return seconds >= 0 ? 'just now' : 'in a moment';
  let amount;
  let unit;
  if (magnitude < 3600) [amount, unit] = [Math.max(1, Math.round(magnitude / 60)), 'min'];
  else if (magnitude < 86400) [amount, unit] = [Math.max(1, Math.round(magnitude / 3600)), 'h'];
  else [amount, unit] = [Math.max(1, Math.round(magnitude / 86400)), 'd'];
  return seconds >= 0 ? `${amount} ${unit} ago` : `in ${amount} ${unit}`;
}

const two = value => String(value).padStart(2, '0');

export function absoluteText(ms) {
  const date = new Date(ms);
  return `${date.getFullYear()}-${two(date.getMonth() + 1)}-${two(date.getDate())} ` +
    `${two(date.getHours())}:${two(date.getMinutes())}`;
}
