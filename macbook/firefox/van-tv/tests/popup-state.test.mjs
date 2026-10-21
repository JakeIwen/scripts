import assert from 'node:assert/strict';
import test from 'node:test';
import { formatTime, popupState } from '../popup-state.mjs';
import { defaultStatus } from './receiver-fixture.mjs';

const { player, control } = globalThis.VAN_TV_CONFIG;

test('elapsed time supports hours and handles unavailable or invalid values', () => {
  for (const [input, expected] of [[0, '0:00'], [65.9, '1:05'], [3661, '1:01:01'],
    [null, '—'], [-1, '—'], [Infinity, '—']]) assert.equal(formatTime(input), expected);
});

test('popup offers only meaningful playback actions and exposes the current title and time', () => {
  const states = [
    [player.idle, []], [player.loading, [control.stop]],
    [player.playing, [control.pause, control.stop]],
    [player.paused, [control.resume, control.stop]], [player.stopped, []],
  ];
  for (const [state, actions] of states) {
    const view = popupState({ ...defaultStatus, player: state });
    assert.deepEqual(view.actions, actions);
    assert.equal(view.state, state);
    assert.equal(view.title, defaultStatus.video.title);
    assert.equal(view.time, '0:45 / 3:00');
  }
  const offline = popupState({ ...defaultStatus, ok: false, video: null });
  assert.equal(offline.state, 'Receiver not running');
  assert.equal(offline.title, 'No video');
  assert.deepEqual(offline.actions, []);
});
