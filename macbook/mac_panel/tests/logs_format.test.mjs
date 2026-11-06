import assert from 'node:assert/strict';
import test from 'node:test';

import {formatBytes, formatGrowth, hasChanges, policyBody, policyText, problems, problemsFromError, validateAddPath, validatePolicy, bySize} from '../static/logs_format.mjs';

const MB = 1024 * 1024;
const entry = (path, bytes, extra = {}) => ({path, bytes, state: 'ok', note: null, policy: {max_mb: 10, keep: 5, compress: true, enabled: true, override: false}, ...extra});
const data = (logs, over = {}) => ({service: {installed: true, loaded: true}, logs, totals: {bytes: 0, archived_bytes: 0}, defaults: {warn_total_mb: 500}, ...over});

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

test('problems flag a file past twice its limit, refusals, a stopped agent and a large total', () => {
  assert.deepEqual(problems(data([entry('/ok.log', 15 * MB)])), []);
  const found = problems(data([entry('/big.log', 21 * MB), entry('/no.log', 1, {state: 'refused', note: 'symlink'})],
    {service: {installed: true, loaded: false}, totals: {bytes: 400 * MB, archived_bytes: 200 * MB}}));
  assert.deepEqual(found.map(x => x.level), ['warn', 'error', 'warn', 'warn']);
  assert.match(found[0].text, /not running/);
  assert.equal(found[1].key, '/big.log');
  assert.match(found[2].text, /no\.log was refused: symlink/);
  assert.match(found[3].text, /over the 500 MB/);
  assert.equal(problems(data([entry('/x', 99 * MB, {policy: {...entry('/x', 1).policy, enabled: false}})])).length, 0);
});

test('policy form checks both fields and sends only what changed', () => {
  assert.deepEqual(Object.keys(validatePolicy({max_mb: '0', keep: '-1'})), ['max_mb', 'keep']);
  assert.deepEqual(validatePolicy({max_mb: '20', keep: '0'}), {});
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
