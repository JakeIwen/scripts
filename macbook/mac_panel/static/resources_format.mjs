// Number and time formatting for the Resources page. Pure, so Node tests it directly.
import {PLACEHOLDER} from './dom_core.mjs';

const KIB = 1024;
const UNITS = ['B', 'KB', 'MB', 'GB', 'TB'];
const SECONDS = {minute: 60, hour: 3600, day: 86400};
const LSTART_PARTS = 5;
const MONTH_FIELD = 1;
const DAY_FIELD = 2;
const TIME_FIELD = 3;
const YEAR_FIELD = 4;

const missing = value => value === null || value === undefined || !Number.isFinite(Number(value));

/** Bytes as KB, MB or GB with one decimal below 10, whole numbers above; the placeholder for a missing value. */
export function formatBytes(value) {
  if (missing(value)) return PLACEHOLDER;
  let amount = Number(value);
  let unit = 0;
  while (amount >= KIB && unit < UNITS.length - 1) {
    amount /= KIB;
    unit += 1;
  }
  const digits = unit === 0 || amount >= 10 ? 0 : 1;
  return `${amount.toFixed(digits)} ${UNITS[unit]}`;
}

/** A percentage with no decimals from 10 up and one below, so a quiet 0.4 % does not read as 0. */
export function formatPercent(value) {
  if (missing(value)) return PLACEHOLDER;
  const amount = Number(value);
  return `${amount >= 10 ? Math.round(amount) : amount.toFixed(1)} %`;
}

/** Seconds as the two largest units: "2 d 3 h", "3 h 12 min", "45 s". */
export function formatDuration(seconds) {
  if (missing(seconds) || seconds < 0) return PLACEHOLDER;
  const total = Math.floor(seconds);
  if (total < SECONDS.minute) return `${total} s`;
  const days = Math.floor(total / SECONDS.day);
  const hours = Math.floor((total % SECONDS.day) / SECONDS.hour);
  const minutes = Math.floor((total % SECONDS.hour) / SECONDS.minute);
  if (days) return hours ? `${days} d ${hours} h` : `${days} d`;
  if (hours) return minutes ? `${hours} h ${minutes} min` : `${hours} h`;
  return `${minutes} min`;
}

/**
 * Milliseconds for a process start token. The backend reads `ps lstart` under TZ=UTC ("Tue Nov 17 08:00:00 2026"),
 * but an ISO stamp or epoch seconds parse too. Null when the token is not a time: it stays an opaque identity.
 */
export function startedMs(started) {
  if (started === null || started === undefined || started === '') return null;
  if (typeof started === 'number') return started * 1000;
  const parts = String(started).trim().split(/\s+/);
  if (parts.length === LSTART_PARTS) {
    const ms = Date.parse(`${parts[MONTH_FIELD]} ${parts[DAY_FIELD]} ${parts[YEAR_FIELD]} ${parts[TIME_FIELD]} UTC`);
    return Number.isNaN(ms) ? null : ms;
  }
  const ms = Date.parse(String(started));
  return Number.isNaN(ms) ? null : ms;
}

export function formatAge(started, now = Date.now()) {
  const ms = startedMs(started);
  return ms === null ? PLACEHOLDER : formatDuration((now - ms) / 1000);
}

/** A working folder with the home directory shortened to ~, the way a shell prompt shows it; null stays null. */
export const shortFolder = folder => (folder ? String(folder).replace(/^\/(?:Users|home)\/[^/]+(?=\/|$)/, '~') : null);

export const plural = (count, singular, many = `${singular}s`) => `${count} ${count === 1 ? singular : many}`;
