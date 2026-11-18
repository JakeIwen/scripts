import assert from 'node:assert/strict';
import test from 'node:test';

const {formatAge, formatBytes, formatDuration, formatPercent, plural, startedMs} = await import('../static/resources_format.mjs');

test('bytes scale to KB, MB and GB with one decimal below 10', () => {
  assert.equal(formatBytes(0), '0 B');
  assert.equal(formatBytes(512), '512 B');
  assert.equal(formatBytes(1536), '1.5 KB');
  assert.equal(formatBytes(300 * 1024 ** 2), '300 MB');
  assert.equal(formatBytes(1.4 * 1024 ** 3), '1.4 GB');
  assert.equal(formatBytes(32 * 1024 ** 3), '32 GB');
});

test('a missing value is the placeholder, never zero', () => {
  for (const format of [formatBytes, formatPercent, formatDuration]) {
    assert.equal(format(null), '—');
    assert.equal(format(undefined), '—');
    assert.equal(format(Number.NaN), '—');
  }
});

test('percent keeps one decimal under 10 and may pass 100', () => {
  assert.equal(formatPercent(0.4), '0.4 %');
  assert.equal(formatPercent(37.5), '38 %');
  assert.equal(formatPercent(215), '215 %');
});

test('durations show the two largest units', () => {
  assert.equal(formatDuration(45), '45 s');
  assert.equal(formatDuration(60 * 12), '12 min');
  assert.equal(formatDuration(3 * 3600), '3 h');
  assert.equal(formatDuration(3 * 3600 + 12 * 60 + 5), '3 h 12 min');
  assert.equal(formatDuration(2 * 86400 + 3 * 3600), '2 d 3 h');
  assert.equal(formatDuration(-5), '—');
});

test('start tokens parse as UTC ps lstart text, ISO or epoch seconds; anything else has no age', () => {
  const expected = Date.UTC(2026, 10, 17, 8, 0, 0);
  assert.equal(startedMs('Tue Nov 17 08:00:00 2026'), expected);
  assert.equal(startedMs('2026-11-17T08:00:00Z'), expected);
  assert.equal(startedMs(expected / 1000), expected);
  assert.equal(startedMs('opaque-token'), null);
  assert.equal(startedMs(null), null);
  assert.equal(formatAge('Tue Nov 17 08:00:00 2026', expected + 90 * 60 * 1000), '1 h 30 min');
  assert.equal(formatAge('opaque-token'), '—');
});

test('plural picks the form by count', () => {
  assert.equal(plural(1, 'session'), '1 session');
  assert.equal(plural(12, 'process', 'processes'), '12 processes');
  assert.equal(plural(0, 'session'), '0 sessions');
});
