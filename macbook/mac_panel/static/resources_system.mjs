// The System card: health, four gauges of what is left, and the load, pressure, thermal and power readings.
// A probe the backend could not read shows as unavailable, never as zero.
import {gauge} from './charts.mjs';
import {card, details, info, kv, node, tag} from './panel_dom.mjs';
import {RESOURCES_COPY as COPY} from './section_copy.mjs';
import {Level, status} from './status_cell.mjs';
import {formatBytes, formatDuration, formatPercent} from './resources_format.mjs';

const COPY_SYSTEM = COPY.system;
const WHOLE = 100;
const PRESSURE_NAMES = {1: 'normal', 2: 'warn', 4: 'critical'};

// The backend computes health.level once; the page only maps its words onto the shared Level vocabulary.
export const HEALTH_LEVEL = Object.freeze({ok: Level.OK, warn: Level.WARN, error: Level.ERROR, unknown: Level.UNKNOWN});
export const healthLevel = health => HEALTH_LEVEL[health?.level] ?? Level.UNKNOWN;

const known = value => value !== null && value !== undefined && Number.isFinite(Number(value));

/** Gauge inputs as the amount left of a maximum, or null when either side is unreadable. */
const GAUGES = [
  {key: 'cpu', format: formatPercent, read: system => (known(system.cpu_percent) ? {value: WHOLE - system.cpu_percent, max: WHOLE} : null)},
  {key: 'memory', format: formatBytes, read: ({memory}) => (memory && known(memory.total) && known(memory.used)
    ? {value: memory.total - memory.used, max: memory.total} : null)},
  {key: 'swap', format: formatBytes, read: ({memory}) => {
    if (!memory || !known(memory.swap_total) || !known(memory.swap_used)) return null;
    return memory.swap_total > 0 ? {value: memory.swap_total - memory.swap_used, max: memory.swap_total} : {note: COPY_SYSTEM.noSwap};
  }},
  {key: 'disk', format: formatBytes, read: ({disk}) => (disk && known(disk.total) && known(disk.free) ? {value: disk.free, max: disk.total} : null)},
];

function gaugeBlock(spec, system) {
  const label = COPY_SYSTEM.gauges[spec.key];
  const reading = spec.read(system);
  if (reading?.value !== undefined) return gauge({...reading, label, format: spec.format});
  const row = node('div', null, 'gauge-missing');
  row.append(node('span', label, 'gauge-label'), node('span', reading?.note ?? COPY_SYSTEM.unavailable, 'muted'));
  return row;
}

// Memory pressure arrives as a word, or as the kernel's level number; anything else is shown as it came.
export function pressureText(pressure) {
  if (!known(pressure) && typeof pressure !== 'string') return null;
  return PRESSURE_NAMES[pressure] ?? String(pressure);
}

// Thermal is a word or an object carrying one; the page does not judge it, only shows it.
export function thermalText(thermal) {
  if (thermal === null || thermal === undefined) return null;
  if (typeof thermal === 'object') return thermal.text ?? thermal.status ?? thermal.state ?? thermal.level ?? null;
  return String(thermal);
}

export function powerText(power) {
  if (!power) return null;
  const parts = [power.source === 'battery' ? COPY_SYSTEM.power.battery : power.source === 'ac' ? COPY_SYSTEM.power.ac : power.source];
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
  const pressure = pressureText(system.memory?.pressure);
  const unavailable = COPY_SYSTEM.unavailable;
  const text = value => value ?? node('span', unavailable, 'muted');
  return kv([
    [COPY_SYSTEM.terms.load, text(loadText(system.load))],
    [COPY_SYSTEM.terms.pressure, text(pressure ? tag(pressure) : null)],
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
