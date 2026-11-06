import assert from 'node:assert/strict';
import test from 'node:test';

import {formatMegabytes, megabytePoints, formatBytes, formatGrowth, hasChanges, policyBody, policyText, problemsFromError, validateAddPath, validatePolicy, bySize} from '../static/logs_format.mjs';

const MB = 1024 * 1024;
const entry = (path, bytes, extra = {}) => ({path, bytes, state: 'ok', note: null, policy: {max_mb: 10, keep: 5, compress: true, enabled: true, override: false}, ...extra});

test('sizes scale to the unit a person reads', () => {
  assert.equal(formatBytes(512), '512 B');
  assert.equal(formatBytes(1536), '1.5 KB');
  assert.equal(formatBytes(35 * MB), '35 MB');
  assert.equal(formatBytes(150 * MB), '150 MB');
  assert.equal(formatBytes(3 * 1024 * MB), '3 GB');
  assert.equal(formatBytes(null), '—');
});

test('growth reads as a signed rate, flat when negligible, a dash when unknown', () => {
  assert.equal(formatGrowth(2.34), '+2.3 MB/day');
  assert.equal(formatGrowth(-1), '−1 MB/day');
  assert.equal(formatGrowth(0.01), 'flat');
  assert.equal(formatGrowth(null), '—');
});

test('policy text names the limit, archives and compression', () => {
  assert.equal(policyText(entry('/a', 1).policy), '10 MB · keep 5 · gz');
  assert.equal(policyText({...entry('/a', 1).policy, enabled: false}), 'rotation off');
});

test('largest file sorts first and ties are stable', () => {
  assert.deepEqual(bySize([entry('/b', 1), entry('/a', 1), entry('/c', 9)]).map(x => x.path), ['/c', '/a', '/b']);
});

test('policy form checks both fields and sends only what changed', () => {
  assert.deepEqual(Object.keys(validatePolicy({max_mb: '0', keep: '-1'})), ['max_mb', 'keep']);
  assert.deepEqual(validatePolicy({max_mb: '20', keep: '0'}), {});
  assert.deepEqual(Object.keys(validatePolicy({max_mb: '2.5', keep: '1'})), ['max_mb']);
  assert.deepEqual(Object.keys(validatePolicy({max_mb: '501', keep: '1'})), ['max_mb']);
  assert.deepEqual(Object.keys(validatePolicy({max_mb: '', keep: '1'})), ['max_mb']);
  const initial = entry('/a', 1).policy;
  const body = policyBody('/a', 'rev', {max_mb: '20', keep: '5', compress: true, enabled: false}, initial);
  assert.deepEqual(body, {action: 'policy', path: '/a', revision: 'rev', max_mb: 20, enabled: false});
  assert.equal(hasChanges(body), true);
  assert.equal(hasChanges(policyBody('/a', 'rev', {max_mb: '10', keep: '5', compress: true, enabled: true}, initial)), false);
});

test('add path must be absolute and server messages map to a field', () => {
  assert.equal(validateAddPath('/Users/x/a.log'), null);
  assert.ok(validateAddPath('logs/a.log'));
  assert.deepEqual(Object.keys(problemsFromError('max_mb out of range')), ['max_mb']);
  assert.deepEqual(Object.keys(problemsFromError('keep must be 0-30')), ['keep']);
  assert.deepEqual(Object.keys(problemsFromError('path is outside HOME')), ['form']);
});

test('footprint points convert to megabytes and niceTicks gives round MB ticks', async () => {
  const {niceTicks} = await import('../static/chart_scale.mjs');
  const points = megabytePoints([{ts: 1, value: 0}, {ts: 2, value: null}, {ts: 3, value: 88 * 1024 * 1024}]);
  assert.deepEqual(points, [{ts: 1, value: 0}, {ts: 3, value: 88}]);
  const ticks = niceTicks(0, 88, 5).ticks;
  assert.deepEqual(ticks, [0, 25, 50, 75, 100]);
  assert.deepEqual(ticks.map(formatMegabytes), ['0 MB', '25 MB', '50 MB', '75 MB', '100 MB']);
});
