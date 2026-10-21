import assert from 'node:assert/strict';
import test from 'node:test';
import { createReceiverClient, decodeStatus } from '../receiver-client.mjs';
import { defaultStatus, startReceiver, videoId } from './receiver-fixture.mjs';

test('background HTTP round trip sends JSON casts and controls without cookies or pairing data', async t => {
  const fixture = await startReceiver(t);
  const receiver = createReceiverClient({ baseUrls: [fixture.url] });
  const status = await receiver.status();
  assert.equal(status.video.title, 'A test video');
  assert.equal(status.pairing, undefined);
  assert.deepEqual(await receiver.cast(videoId, 12.5), { videoId, position: 12.5 });
  assert.deepEqual(await receiver.control('pause'), { action: 'pause', done: true });
  const posts = fixture.requests.filter(request => request.method === 'POST');
  assert.deepEqual(posts.map(request => [request.path, request.body]), [
    ['/api/cast', { videoId, position: 12.5 }], ['/api/control', { action: 'pause' }],
  ]);
  for (const request of posts) {
    assert.equal(request.headers['content-type'], 'application/json');
    assert.equal(request.headers.cookie, undefined);
  }
});

test('unreachable primary falls back on a read and performs exactly one cast', async t => {
  const fixture = await startReceiver(t);
  const receiver = createReceiverClient({ baseUrls: ['http://127.0.0.1:1', fixture.url] });
  await receiver.cast(videoId);
  assert.deepEqual(fixture.requests.map(request => request.path), ['/api/status', '/api/cast']);
  assert.equal(fixture.requests[1].body.position, 0);
});

test('ambiguous POST connection loss is reported and never replayed through another address', async t => {
  const primary = await startReceiver(t, { respond(request, response) {
    if (request.method !== 'POST') return false;
    response.destroy();
    return true;
  } });
  const fallback = await startReceiver(t);
  const receiver = createReceiverClient({ baseUrls: [primary.url, fallback.url] });
  await assert.rejects(receiver.cast(videoId, 20), /request may have been accepted/);
  assert.equal(primary.requests.filter(request => request.method === 'POST').length, 1);
  assert.equal(fallback.requests.length, 0);
});

test('invalid IDs, positions and actions fail before any network request', async t => {
  const fixture = await startReceiver(t);
  const receiver = createReceiverClient({ baseUrls: [fixture.url] });
  for (const [id, position] of [['bad', 0], [videoId, -1], [videoId, Infinity], [videoId, '12']]) {
    await assert.rejects(receiver.cast(id, position), /Invalid YouTube/);
  }
  await assert.rejects(receiver.control('seek'), /Unknown playback action/);
  assert.equal(fixture.requests.length, 0);
});

test('receiver HTTP errors are surfaced without endpoint failover', async t => {
  const primary = await startReceiver(t, { respond(request, response) {
    if (request.method !== 'POST') return false;
    response.writeHead(503); response.end('{}'); return true;
  } });
  const fallback = await startReceiver(t);
  const receiver = createReceiverClient({ baseUrls: [primary.url, fallback.url] });
  await assert.rejects(receiver.cast(videoId), /receiver is not running/);
  assert.equal(fallback.requests.length, 0);
});

test('bounded status reads abort a receiver that never answers', async t => {
  const fixture = await startReceiver(t, { respond: () => true });
  const receiver = createReceiverClient({ baseUrls: [fixture.url], timeoutMs: 30 });
  await assert.rejects(receiver.status(), /Van TV unreachable/);
});

test('malformed status and POST acknowledgements do not count as success', async t => {
  for (const patch of [{ player: 'BOGUS' }, { position: null }, { duration: -1 },
    { ok: 'yes' }, { video: { id: videoId, title: null } }]) {
    assert.throws(() => decodeStatus({ ...defaultStatus, ...patch }), /Invalid Van TV status/);
  }
  const fixture = await startReceiver(t, { respond(request, response) {
    if (request.method !== 'POST') return false;
    response.writeHead(200, { 'Content-Type': 'application/json' });
    response.end('{}'); return true;
  } });
  const receiver = createReceiverClient({ baseUrls: [fixture.url] });
  await assert.rejects(receiver.cast(videoId), /Invalid Van TV cast response/);
  await assert.rejects(receiver.control('stop'), /Invalid Van TV control response/);
});

test('redirects are refused instead of following an unapproved receiver destination', async t => {
  const destination = await startReceiver(t);
  const fixture = await startReceiver(t, { respond(request, response) {
    response.writeHead(302, { Location: `${destination.url}/api/status` });
    response.end(); return true;
  } });
  const receiver = createReceiverClient({ baseUrls: [fixture.url] });
  await assert.rejects(receiver.status(), /Van TV unreachable/);
  assert.equal(destination.requests.length, 0);
});
