import test from 'node:test';
import assert from 'node:assert/strict';
import { stopVideoLibrary } from '../src/vlc-handoff.mjs';
import { config } from '../src/config.mjs';

const log = { warn() {} };

test('Handoff prefers the bookmarking HTTP stop route over MPRIS', async () => {
  const result = await stopVideoLibrary({ log, fetcher: async (url, options) => {
    assert.equal(url, config.videoLibraryUrl);
    assert.equal(options.method, 'POST');
    assert.equal(options.body.get('action'), 'stop');
    return Response.json({ ok: true });
  }, run: async command => {
    if (command !== config.vlcActiveCommand) assert.fail('must not bypass library bookmark');
  } });
  assert.equal(result, 'http');
});

test('Handoff falls back to MPRIS Quit then only the library-owned user unit', async () => {
  for (const fetcher of [async () => { throw new Error('offline'); },
    async () => Response.json({ ok: false }, { status: 503 }),
    async () => new Response('bad JSON')]) {
    const commands = [];
    assert.equal(await stopVideoLibrary({ log, fetcher, run: async command => {
      if (command === config.vlcActiveCommand) return;
      commands.push(command);
      if (command === config.vlcQuitCommand) throw new Error('VLC absent');
    } }), 'mpris');
    assert.deepEqual(commands, [config.vlcQuitCommand, config.vlcStopCommand]);
  }
});

test('Handoff fails closed if neither HTTP nor owned-unit stop succeeds', async () => {
  await assert.rejects(stopVideoLibrary({ log,
    fetcher: async () => { throw new Error('offline'); },
    run: async command => {
      if (command !== config.vlcActiveCommand) throw new Error('user bus unavailable');
    },
  }), /user bus unavailable/);
});

test('Handoff does nothing when the library player unit is not active', async () => {
  const commands = [];
  assert.equal(await stopVideoLibrary({ log,
    fetcher: () => assert.fail('no library stop without VLC'),
    run: async command => { commands.push(command); throw new Error('Command exited 3'); },
  }), 'idle');
  assert.deepEqual(commands, [config.vlcActiveCommand]);
});

test('Cancelled casts do not stop VLC through the fallback', async () => {
  const abort = new AbortController();
  abort.abort();
  await assert.rejects(stopVideoLibrary({ log, signal: abort.signal,
    fetcher: async () => { throw new Error('aborted'); },
    run: () => assert.fail('cancelled operation'),
  }), { name: 'AbortError' });
});
