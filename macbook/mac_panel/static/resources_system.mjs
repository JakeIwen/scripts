// The System card: health, four gauges of what is used, and the load, pressure, thermal and power readings.
// A probe the backend could not read shows as unavailable, never as zero.
import {gauge} from './charts.mjs';
import {card, details, info, kv, node, tag} from './panel_dom.mjs';
import {RESOURCES_COPY as COPY} from './section_copy.mjs';
import {Level, status} from './status_cell.mjs';
import {formatBytes, formatDuration, formatPercent} from './resources_format.mjs';

const COPY_SYSTEM = COPY.system;
const WHOLE = 100;
const PRESSURE_LEVEL = {normal: Level.OK, warn: Level.WARN, critical: Level.ERROR};

// The backend computes health.level once; the page only maps its words onto the shared Level vocabulary.
export const HEALTH_LEVEL = Object.freeze({ok: Level.OK, warn: Level.WARN, error: Level.ERROR, unknown: Level.UNKNOWN});
export const healthLevel = health => HEALTH_LEVEL[health?.level] ?? Level.UNKNOWN;

const known = value => value !== null && value !== undefined && Number.isFinite(Number(value));

// Where each bar turns warn and error, as a fraction of what is used. Memory has none: the backend's pressure value
// decides its colour, because a full cache is healthy and the fill alone would cry wolf.
export const GAUGE_THRESHOLDS = Object.freeze({
  cpu: {warn: 0.85, error: 0.95},
  swap: {warn: 0.5, error: 0.8},
  disk: {warn: 0.9, error: 0.95},
});
// A pressure the backend could not read is drawn as unknown, never as the healthy colour.
const PRESSURE_GAUGE_LEVEL = {normal: 'ok', warn: 'warn', critical: 'error'};
const UNKNOWN_GAUGE_LEVEL = 'unknown';

function memoryReading({used, total, pressure}) {
  const level = PRESSURE_GAUGE_LEVEL[pressure];
  if (level) return {value: used, max: total, level};
  return {value: used, max: total, level: UNKNOWN_GAUGE_LEVEL, valueText: COPY_SYSTEM.memoryNoPressure(formatBytes(used), formatBytes(total))};
}

/** Gauge inputs as the amount used of a maximum, or null when either side is unreadable. */
const GAUGES = [
  {key: 'cpu', format: formatPercent, read: system => (known(system.cpu_percent)
    ? {value: system.cpu_percent, max: WHOLE, worse: GAUGE_THRESHOLDS.cpu, valueText: COPY_SYSTEM.cpuUsed(formatPercent(system.cpu_percent))} : null)},
  {key: 'memory', format: formatBytes, read: ({memory}) => (memory && known(memory.total) && known(memory.used)
    ? memoryReading(memory) : null)},
  {key: 'swap', format: formatBytes, read: ({memory}) => {
    if (!memory || !known(memory.swap_total) || !known(memory.swap_used)) return null;
    return memory.swap_total > 0 ? {value: memory.swap_used, max: memory.swap_total, worse: GAUGE_THRESHOLDS.swap} : {note: COPY_SYSTEM.noSwap};
  }},
  {key: 'disk', format: formatBytes, read: ({disk}) => (disk && known(disk.total) && known(disk.free)
    ? {value: disk.total - disk.free, max: disk.total, worse: GAUGE_THRESHOLDS.disk} : null)},
];

/** Each gauge's input (what is used, its maximum, thresholds or forced level), or null / a note; exported for tests. */
export const gaugeReadings = system => Object.fromEntries(GAUGES.map(spec => [spec.key, spec.read(system)]));

function gaugeBlock(spec, system) {
  const label = COPY_SYSTEM.gauges[spec.key];
  const reading = spec.read(system);
  if (reading?.value !== undefined) return gauge({...reading, label, format: spec.format});
  const row = node('div', null, 'gauge-missing');
  row.append(node('span', label, 'gauge-label'), node('span', reading?.note ?? COPY_SYSTEM.unavailable, 'muted'));
  return row;
}

// The backend sends the words normal | warn | critical, thermal as nominal | warning, and power.source as ac | battery | ups
// or the raw lower-case text it read; a word the page has no wording for is shown as it came.
export const pressureTag = pressure => (pressure ? tag(COPY_SYSTEM.pressure[pressure] ?? pressure, PRESSURE_LEVEL[pressure]) : null);
export const thermalText = thermal => (thermal ? COPY_SYSTEM.thermal[thermal] ?? thermal : null);

export function powerText(power) {
  if (!power) return null;
  const parts = [COPY_SYSTEM.power[power.source] ?? power.source];
  if (known(power.percent)) parts.push(`${Math.round(power.percent)} %`);
  if (power.charging !== undefined && power.charging !== null) parts.push(power.charging ? COPY_SYSTEM.power.charging : COPY_SYSTEM.power.notCharging);
  return parts.filter(Boolean).join(' · ') || null;
}

const loadText = load => (Array.isArray(load) && load.length ? load.map(value => Number(value).toFixed(2)).join(' · ') : null);

function memoryBreakdown(memory) {
  const rows = Object.entries(COPY_SYSTEM.memoryParts).map(([key, label]) => [label, formatBytes(memory?.[key])]);
  const body = node('div');
  body.append(kv(rows), node('p', COPY_SYSTEM.info.memory, 'muted small'));
  return details(COPY_SYSTEM.terms.breakdown, body, 'memory-breakdown');
}

function readings(system) {
  const unavailable = COPY_SYSTEM.unavailable;
  const text = value => value ?? node('span', unavailable, 'muted');
  return kv([
    [COPY_SYSTEM.terms.load, text(loadText(system.load))],
    [COPY_SYSTEM.terms.pressure, text(pressureTag(system.memory?.pressure))],
    [COPY_SYSTEM.terms.thermal, text(thermalText(system.thermal))],
    [COPY_SYSTEM.terms.power, text(powerText(system.power))],
    [COPY_SYSTEM.terms.processes, text(known(system.process_count) ? String(system.process_count) : null)],
    [COPY_SYSTEM.terms.uptime, text(known(system.uptime_seconds) ? formatDuration(system.uptime_seconds) : null)],
  ]);
}

export function systemCard(data) {
  const system = data.system ?? {};
  const level = healthLevel(data.health);
  const body = node('div');
  const reasons = data.health?.reasons ?? [];
  body.append(status(level, reasons.length ? reasons.join('; ') : level === Level.OK ? COPY_SYSTEM.healthy : COPY_SYSTEM.unavailable));
  const unavailable = data.health?.unavailable ?? [];
  if (unavailable.length) body.append(node('p', `${COPY_SYSTEM.unavailableProbes}: ${unavailable.join(', ')}`, 'muted small'));
  const gauges = node('div', null, 'gauge-grid');
  GAUGES.forEach(spec => gauges.append(gaugeBlock(spec, system)));
  body.append(gauges, readings(system), memoryBreakdown(system.memory));
  return card({title: COPY_SYSTEM.title, level, summary: null, body, anchor: 'system',
    actions: info(`${COPY_SYSTEM.info.gauges} ${COPY_SYSTEM.info.health}`)});
}
