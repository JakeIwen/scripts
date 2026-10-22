import test from 'node:test';
import assert from 'node:assert/strict';
import { createPlayerControls, audioDelayArguments } from '../src/player-controls.mjs';
import { playerFixture, VIDEO } from './player-fixture.mjs';
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

test('Audio delay accepts exactly one finite absolute or relative number', () => {
  assert.deepEqual(audioDelayArguments({ delay: 0 }), { delay: 0 });
  assert.deepEqual(audioDelayArguments({ delay: -0.12 }), { delay: -0.12 });
  assert.deepEqual(audioDelayArguments({ offset: 0.05 }), { offset: 0.05 });
  assert.deepEqual(audioDelayArguments({ offset: 0 }), { offset: 0 });
  assert.equal(audioDelayArguments({}), null);
  assert.equal(audioDelayArguments({ delay: 0, offset: 0 }), null);
  for (const value of [null, '0.1', false, [], {}, NaN, Infinity, -Infinity]) {
    assert.equal(audioDelayArguments({ delay: value }), null);
    assert.equal(audioDelayArguments({ offset: value }), null);
  }
});

test('Audio delay controls adjust the latest setting while idle or playing', async t => {
  const { player, instances } = playerFixture();
  t.after(() => player.endSession());
  const controls = createPlayerControls(player);
  assert.equal(await controls.audio_delay({ delay: 0.12 }), true);
  assert.equal(player.audioDelay, 0.12);
  assert.equal(await controls.audio_delay({ offset: -0.05 }), true);
  assert.equal(player.audioDelay, 0.07);
  assert.equal(instances.length, 0);
  await player.play(VIDEO);
  await controls.audio_delay({ offset: 0.05 });
  assert.equal(player.audioDelay, 0.12);
  assert.equal(instances[0].properties.get('audio-delay'), 0.12);
  await controls.audio_delay({ offset: 5 });
  assert.equal(player.audioDelay, 1);
  await controls.audio_delay({ offset: -5 });
  assert.equal(player.audioDelay, -1);
  await controls.audio_delay({ delay: 0 });
  assert.equal(player.audioDelay, 0);
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
