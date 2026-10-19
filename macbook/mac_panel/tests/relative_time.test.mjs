import assert from 'node:assert/strict';
import test from 'node:test';

import {absoluteText, parseStamp, relativeText} from '../static/relative_time.mjs';

test('parseStamp accepts epoch seconds, space-separated and ISO local strings, and rejects blanks', () => {
  assert.equal(parseStamp(1700000000), 1700000000000);
  assert.equal(parseStamp('2026-10-19 02:54:27'), new Date('2026-10-19T02:54:27').valueOf());
  assert.equal(parseStamp('2026-10-22T00:00'), new Date('2026-10-22T00:00').valueOf());
  for (const blank of [null, undefined, '', 'never']) assert.equal(parseStamp(blank), null);
});

test('relativeText rounds to the nearest unit in both directions', () => {
  const now = 1_700_000_000_000;
  assert.equal(relativeText(now - 10_000, now), 'just now');
  assert.equal(relativeText(now + 10_000, now), 'in a moment');
  assert.equal(relativeText(now - 50_000, now), '1 min ago');
  assert.equal(relativeText(now - 10 * 60_000, now), '10 min ago');
  assert.equal(relativeText(now - 3 * 3_600_000, now), '3 h ago');
  assert.equal(relativeText(now + 2 * 86_400_000, now), 'in 2 d');
  assert.equal(relativeText(now - 27 * 86_400_000, now), '27 d ago');
});

test('absoluteText is local time at minute precision, zero padded', () => {
  assert.equal(absoluteText(new Date(2026, 9, 19, 2, 54, 27).valueOf()), '2026-10-19 02:54');
});
