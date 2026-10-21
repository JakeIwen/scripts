import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:net';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { once } from 'node:events';
import { MpvIpc } from '../src/mpv-ipc.mjs';

async function fixture(t, handler, timeoutMs = 200) {
  const directory = await mkdtemp(join(tmpdir(), 'yt-ipc-'));
  const path = join(directory, 'ipc');
  const sockets = new Set();
  const server = createServer(socket => {
    sockets.add(socket);
    let buffer = '';
    socket.on('data', chunk => {
      buffer += chunk;
      let newline;
      while ((newline = buffer.indexOf('\n')) >= 0) {
        const request = JSON.parse(buffer.slice(0, newline));
        buffer = buffer.slice(newline + 1);
        handler(socket, request);
      }
    });
  });
  server.listen(path);
  await once(server, 'listening');
  const client = new MpvIpc(path, { timeoutMs });
  t.after(async () => {
    client.close();
    for (const socket of sockets) socket.destroy();
    await new Promise(resolve => server.close(resolve));
    await rm(directory, { recursive: true });
  });
  await client.connect();
  return client;
}

test('IPC correlates concurrent responses and fragmented events', async t => {
  const requests = [];
  const client = await fixture(t, (socket, request) => {
    requests.push(request);
    if (requests.length !== 2) return;
    socket.write('{"event":"property-');
    socket.write('change","name":"time-pos","data":12}\n');
    for (const item of requests.reverse()) socket.write(`${JSON.stringify({ request_id: item.request_id,
      error: 'success', data: item.command[1] })}\n`);
  });
  const event = once(client, 'event');
  assert.deepEqual(await Promise.all([
    client.command(['get_property', 'duration']), client.command(['get_property', 'time-pos']),
  ]), ['duration', 'time-pos']);
  assert.equal((await event)[0].data, 12);
});

test('IPC rejects mpv command errors', async t => {
  const client = await fixture(t, (socket, request) => socket.write(`${JSON.stringify({
    request_id: request.request_id, error: 'property unavailable',
  })}\n`));
  await assert.rejects(client.command(['get_property', 'missing']), /property unavailable/);
});

test('IPC has bounded timeouts and rejects requests on socket loss', async t => {
  const client = await fixture(t, (socket, request) => {
    if (request.command[0] === 'disconnect') socket.destroy();
  }, 40);
  await assert.rejects(client.command(['hang']), /timeout/);
  await assert.rejects(client.command(['disconnect']), /disconnected/);
});
