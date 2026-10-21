import test from 'node:test';
import assert from 'node:assert/strict';
import { createPlayerControls } from '../src/player-controls.mjs';
import { PlayerStatus } from '../src/mpv-player.mjs';

function fakePlayer(state) {
  const calls = [];
  const record = name => async (...args) => { calls.push([name, ...args]); return true; };
  return { calls, video: { id: 'abcdefghijk' }, position: 100, duration: 600,
    queue: { hasNext: true, hasPrevious: true }, ...state,
    pause: record('pause'), resume: record('resume'), seek: record('seek'),
    next: record('next'), previous: record('previous'), reset: record('reset') };
}

test('Toggle pauses while playing, resumes while paused, and does nothing otherwise', async () => {
  const playing = fakePlayer({ status: PlayerStatus.PLAYING });
  await createPlayerControls(playing).toggle();
  const paused = fakePlayer({ status: PlayerStatus.PAUSED });
  await createPlayerControls(paused).toggle();
  const idle = fakePlayer({ status: PlayerStatus.IDLE });
  assert.equal(await createPlayerControls(idle).toggle(), false);
  assert.deepEqual([playing.calls, paused.calls, idle.calls], [[['pause']], [['resume']], []]);
});

test('Seek resolves offsets from the current position and clamps to the video', async () => {
  const player = fakePlayer({ status: PlayerStatus.PLAYING });
  const controls = createPlayerControls(player);
  await controls.seek({ offset: -20 });
  await controls.seek({ offset: -300 });
  await controls.seek({ offset: 600 });
  await controls.seek({ position: 42 });
  assert.deepEqual(player.calls, [['seek', 80], ['seek', 0], ['seek', 600], ['seek', 42]]);
  assert.equal(await createPlayerControls(fakePlayer({ video: null })).seek({ offset: 20 }), false);
});

test('Without a queue, Next keeps playing and Previous restarts the video', async () => {
  const player = fakePlayer({ status: PlayerStatus.PLAYING, queue: { hasNext: false, hasPrevious: false } });
  const controls = createPlayerControls(player);
  assert.equal(await controls.next(), false);
  await controls.previous();
  assert.deepEqual(player.calls, [['seek', 0]]);
});

test('Stop resets the queue; next and previous go through the library', async () => {
  const player = fakePlayer({ status: PlayerStatus.PLAYING });
  const controls = createPlayerControls(player);
  assert.equal(await controls.stop(), true);
  await controls.next();
  await controls.previous();
  assert.deepEqual(player.calls, [['reset'], ['next'], ['previous']]);
});
