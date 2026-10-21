import test from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { get } from 'node:http';
import { createStatusServer } from '../src/status-server.mjs';

test('Cast route accepts extension and header-less senders, validates ids, reports readiness', async t => {
  const casts = [];
  let ready = true;
  const server = await createStatusServer({ snapshot: () => ({ ok: true }),
    pairing: { snapshot: () => ({}), refresh: () => true },
    cast: async (videoId, position) => {
      if (!ready) throw new Error('Receiver not running');
      casts.push([videoId, position]);
    },
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => new Promise(resolve => { server.close(resolve); server.closeAllConnections(); }));
  const base = `http://127.0.0.1:${server.address().port}`;
  const post = (body, headers = {}) => fetch(`${base}/api/cast`, { method: 'POST',
    headers: { 'Content-Type': 'application/json', ...headers }, body });
  const accepted = await post('{"videoId":"dQw4w9WgXcQ","position":12.5}', { Origin: 'moz-extension://abc' });
  assert.equal(accepted.status, 202);
  assert.deepEqual(await accepted.json(), { videoId: 'dQw4w9WgXcQ', position: 12.5 });
  assert.equal((await post('{"videoId":"jNQXAC9IVRw"}')).status, 202);
  assert.equal((await post('{"videoId":"jNQXAC9IVRw"}', { Origin: 'null' })).status, 202);
  assert.equal((await post('{"videoId":"../etc/passwd"}')).status, 400);
  assert.equal((await post('{"videoId":"dQw4w9WgXcQ","position":-1}')).status, 400);
  assert.equal((await post('[]')).status, 400);
  assert.equal((await post('{"videoId":"dQw4w9WgXcQ"}', { Origin: 'https://evil.example' })).status, 403);
  assert.equal((await fetch(`${base}/api/cast`, { method: 'POST', body: 'videoId=dQw4w9WgXcQ' })).status, 403);
  ready = false;
  assert.equal((await post('{"videoId":"dQw4w9WgXcQ"}')).status, 503);
  assert.deepEqual(casts, [['dQw4w9WgXcQ', 12.5], ['jNQXAC9IVRw', 0], ['jNQXAC9IVRw', 0]]);
});

test('Control route runs only known actions from the same origin', async t => {
  const actions = [];
  const server = await createStatusServer({ snapshot: () => ({ ok: true }),
    pairing: { snapshot: () => ({}), refresh: () => true },
    control: async (action, fields) => { actions.push([action, fields]); return true; },
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => new Promise(resolve => { server.close(resolve); server.closeAllConnections(); }));
  const base = `http://127.0.0.1:${server.address().port}`;
  const post = (body, origin = base) => fetch(`${base}/api/control`, { method: 'POST',
    headers: { Origin: origin, 'Content-Type': 'application/json' }, body });
  const paused = await post('{"action":"pause"}');
  assert.deepEqual(await paused.json(), { action: 'pause', done: true });
  assert.equal((await post('{"action":"reboot"}')).status, 400);
  assert.equal((await post('not json')).status, 400);
  assert.equal((await post('{"action":"stop"}', 'https://evil.example')).status, 403);
  assert.equal((await post('{"action":"seek","offset":-20}')).status, 200);
  assert.equal((await post('{"action":"seek","position":95.5}')).status, 200);
  for (const bad of ['{"action":"seek"}', '{"action":"seek","position":-1}',
    '{"action":"seek","position":10,"offset":5}', '{"action":"seek","offset":"20"}']) {
    assert.equal((await post(bad)).status, 400, bad);
  }
  assert.deepEqual(actions, [['pause', {}], ['seek', { offset: -20 }], ['seek', { position: 95.5 }]]);
});

test('Status serves code locally, keeps health code-free and rejects cross-origin refresh', async t => {
  let refreshes = 0;
  const server = await createStatusServer({ snapshot: () => ({ ok: true, receiver: 'running', player: 'IDLE' }),
    pairing: { snapshot: () => ({ code: '123 456 789 012' }), refresh: () => { refreshes++; return true; } },
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => new Promise(resolve => { server.close(resolve); server.closeAllConnections(); }));
  const base = `http://127.0.0.1:${server.address().port}`;
  const health = await fetch(`${base}/health`);
  assert.equal(health.status, 200);
  assert.equal((await health.text()).includes('123'), false);
  assert.equal((await (await fetch(`${base}/api/status`)).json()).pairing.code, '123 456 789 012');
  const page = await fetch(base);
  assert.match(await page.text(), /<title>Van TV<\/title>/);
  assert.equal(page.headers.get('cache-control'), 'no-store');
  assert.equal((await fetch(`${base}/api/pairing`, { method: 'POST', headers: {
    Origin: 'https://evil.example', 'Content-Type': 'application/json',
  }, body: '{}' })).status, 403);
  assert.equal((await fetch(`${base}/api/pairing`, { method: 'POST', headers: {
    Origin: base, 'Content-Type': 'application/json',
  }, body: '{}' })).status, 202);
  assert.equal(refreshes, 1);
  const foreignHost = await new Promise((resolve, reject) => {
    get(`${base}/health`, { headers: { Host: 'evil.example' } }, response => {
      response.resume(); resolve(response.statusCode);
    }).on('error', reject);
  });
  assert.equal(foreignHost, 403);
});
