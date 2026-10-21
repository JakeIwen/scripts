import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { fetchSegments, skipTarget, SponsorSkipper } from '../src/sponsor-block.mjs';

const videoId = 'abcdefghijk';
const log = { warn() {} };

test('SponsorBlock sends only the four-character hash prefix and filters exact video/category', async () => {
  const segments = await fetchSegments(videoId, { fetcher: async url => {
    assert.equal(url.pathname.split('/').at(-1), createHash('sha256').update(videoId).digest('hex').slice(0, 4));
    assert.equal(url.href.includes(videoId), false);
    assert.deepEqual(JSON.parse(url.searchParams.get('categories')), ['sponsor', 'selfpromo']);
    return Response.json([
      { videoID: 'other-video', segments: [{ category: 'sponsor', segment: [0, 99] }] },
      { videoID: videoId, segments: [
        { category: 'selfpromo', segment: [30, 40] },
        { category: 'sponsor', actionType: 'skip', segment: [10, 20] },
        { category: 'intro', segment: [0, 8] },
        { category: 'sponsor', actionType: 'mute', segment: [2, 8] },
        { category: 'sponsor', segment: [-1, 8] },
        { category: 'sponsor', segment: [12, 3] },
        { category: 'sponsor', segment: ['2', 8] },
      ] },
    ]);
  }, log });
  assert.deepEqual(segments, [[10, 20], [30, 40]]);
});

test('SponsorBlock failures, 404 and malformed records do not block playback', async () => {
  for (const fetcher of [async () => { throw new Error('offline'); },
    async () => new Response('', { status: 404 }),
    async () => new Response('', { status: 500 }),
    async () => Response.json({ error: 'bad' })]) {
    assert.deepEqual(await fetchSegments(videoId, { fetcher, log }), []);
  }
});

test('Skip logic merges overlapping segments but preserves real gaps and end boundaries', () => {
  const segments = [[10, 20], [18, 25], [30, 40]];
  assert.equal(skipTarget(9, segments), null);
  assert.equal(skipTarget(10, segments), 25);
  assert.equal(skipTarget(25, segments), null);
  assert.equal(skipTarget(35, segments), 40);
  assert.equal(skipTarget(40, segments), null);
});

test('Skipper suppresses overlapping in-flight seeks and remains usable after failures', async () => {
  const skipper = new SponsorSkipper([[10, 20]]);
  let finish;
  const pending = skipper.tick(12, target => new Promise(resolve => {
    assert.equal(target, 20); finish = resolve;
  }));
  assert.equal(await skipper.tick(13, () => assert.fail('duplicate seek')), false);
  finish(true);
  assert.equal(await pending, true);
  await assert.rejects(skipper.tick(12, async () => { throw new Error('seek failed'); }));
  assert.equal(await skipper.tick(12, async () => true), true);
});
