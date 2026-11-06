// Pure helpers for the Logs section (sizes, growth, limits, policy form checks); no DOM, so Node can test them.
export const BYTES_PER_MB = 1024 * 1024;
const BYTES_PER_KB = 1024;
const BYTES_PER_GB = 1024 * BYTES_PER_MB;
// A file this many times past its limit means rotation is failing, not merely due (mirrors logs_adapter.OVERSHOOT).
export const OVERSHOOT = 2;
const MAX_MB_LIMIT = 500;
const KEEP_LIMIT = 30;
const ABSOLUTE_PATH = /^\/[^\u0000-\u001f]*$/;

function scaled(value, unit) {
  return `${value >= 100 ? Math.round(value) : Math.round(value * 10) / 10} ${unit}`;
}

/** 1536 -> "1.5 KB"; MB is 1024 * 1024 bytes, as the rotator's limits are. */
export function formatBytes(bytes) {
  if (bytes === null || bytes === undefined) return '—';
  if (bytes >= BYTES_PER_GB) return scaled(bytes / BYTES_PER_GB, 'GB');
  if (bytes >= BYTES_PER_MB) return scaled(bytes / BYTES_PER_MB, 'MB');
  if (bytes >= BYTES_PER_KB) return scaled(bytes / BYTES_PER_KB, 'KB');
  return `${bytes} B`;
}

/** Growth per day from the rotator (MB/day); null means it has no earlier size to compare with. */
export function formatGrowth(mbPerDay) {
  if (mbPerDay === null || mbPerDay === undefined) return '—';
  if (Math.abs(mbPerDay) < 0.05) return 'flat';
  return `${mbPerDay > 0 ? '+' : '−'}${scaled(Math.abs(mbPerDay), 'MB')}/day`;
}

export function policyText(policy) {
  if (!policy.enabled) return 'rotation off';
  return `${policy.max_mb} MB · keep ${policy.keep} · ${policy.compress ? 'gz' : 'plain'}`;
}

export const overshoots = entry => entry.policy.enabled && entry.bytes > OVERSHOOT * entry.policy.max_mb * BYTES_PER_MB;
export const isDue = entry => entry.state === 'due';
export const basename = path => path.slice(path.lastIndexOf('/') + 1) || path;

/** Largest live file first; ties by path so the order is stable across polls. */
export const bySize = logs => [...logs].sort((a, b) => b.bytes - a.bytes || a.path.localeCompare(b.path));

/** Problems the section lists on Overview, each {level, text, key?} where key is a log path to jump to. */
export function problems(data) {
  const found = [];
  const service = data.service ?? {};
  if (service.installed === false) found.push({level: 'warn', text: 'rotator agent is not installed'});
  else if (service.loaded === false) found.push({level: 'warn', text: 'rotator agent is not running'});
  data.logs.forEach(entry => {
    if (overshoots(entry)) {
      found.push({level: 'error', key: entry.path,
        text: `${basename(entry.path)} is ${formatBytes(entry.bytes)}, past ${OVERSHOOT}x its ${entry.policy.max_mb} MB limit; rotation is failing`});
    }
    if (entry.state === 'refused') found.push({level: 'warn', key: entry.path, text: `${basename(entry.path)} was refused${entry.note ? `: ${entry.note}` : ''}`});
  });
  const total = data.totals.bytes + data.totals.archived_bytes;
  if (total > data.defaults.warn_total_mb * BYTES_PER_MB) {
    found.push({level: 'warn', text: `logs total ${formatBytes(total)}, over the ${data.defaults.warn_total_mb} MB warning level`});
  }
  return found;
}

/** values: the editor's strings ('' for empty). Returns {field: message}; empty when the form can be sent. */
export function validatePolicy(values) {
  const result = {};
  const mb = Number(values.max_mb);
  if (values.max_mb === '' || !Number.isFinite(mb) || mb < 1 || mb > MAX_MB_LIMIT) result.max_mb = `Enter a size from 1 to ${MAX_MB_LIMIT} MB.`;
  if (!/^\d+$/.test(values.keep) || Number(values.keep) > KEEP_LIMIT) result.keep = `Keep a whole number of archives from 0 to ${KEEP_LIMIT}.`;
  return result;
}

/** Body for the changed fields only, so an untouched field keeps following the defaults. */
export function policyBody(path, revision, values, initial) {
  const body = {action: 'policy', path, revision};
  if (Number(values.max_mb) !== Number(initial.max_mb)) body.max_mb = Number(values.max_mb);
  if (Number(values.keep) !== Number(initial.keep)) body.keep = Number(values.keep);
  if (values.compress !== initial.compress) body.compress = values.compress;
  if (values.enabled !== initial.enabled) body.enabled = values.enabled;
  return body;
}

export const hasChanges = body => Object.keys(body).length > 3;

export function validateAddPath(text) {
  return ABSOLUTE_PATH.test(text.trim()) ? null : 'Enter an absolute path, starting with /.';
}

/** Maps a server message to {field: message}; one that names no field comes back under `form`. */
export function problemsFromError(message) {
  if (/max[_ -]?mb|size/i.test(message)) return {max_mb: message};
  if (/keep/i.test(message)) return {keep: message};
  return {form: message};
}
