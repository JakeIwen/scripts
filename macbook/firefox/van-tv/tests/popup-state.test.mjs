import assert from 'node:assert/strict';
import test from 'node:test';
import {
  createScrubState, formatTime, popupState, transitionScrub,
} from '../popup-state.mjs';
import { defaultStatus } from './receiver-fixture.mjs';

const { player, control } = globalThis.VAN_TV_CONFIG;

const playbackActions = [
  control.previous, control.seek, control.toggle, control.next, control.stop,
];

test('elapsed time supports hours and handles unavailable or invalid values', () => {
  for (const [input, expected] of [[0, '0:00'], [65.9, '1:05'], [3661, '1:01:01'],
    [null, '—'], [-1, '—'], [Infinity, '—']]) assert.equal(formatTime(input), expected);
});

test('popup enables meaningful controls for every receiver state', () => {
  const states = [
    [player.idle, []],
    [player.loading, [control.stop]],
    [player.playing, playbackActions],
    [player.paused, playbackActions],
    [player.stopped, []],
  ];
  for (const [state, actions] of states) {
    const view = popupState({ ...defaultStatus, player: state });
    assert.deepEqual(view.actions, actions);
    assert.equal(view.state, state);
    assert.equal(view.title, defaultStatus.video.title);
    assert.equal(view.time, '0:45 / 3:00');
    assert.equal(view.scrubEnabled, [player.playing, player.paused].includes(state));
    assert.equal(view.toggleLabel, state === player.playing ? 'Pause' : 'Play');
  }
});

test('scrubbing also requires a video with a positive duration', () => {
  for (const patch of [{ video: null }, { duration: 0 }, { ok: false }]) {
    assert.equal(popupState({ ...defaultStatus, ...patch }).scrubEnabled, false);
  }
  const offline = popupState({ ...defaultStatus, ok: false, video: null });
  assert.equal(offline.state, 'Receiver not running');
  assert.equal(offline.title, 'No video');
  assert.deepEqual(offline.actions, []);
});

test('polls do not move a dragging scrubber and release emits one absolute seek', () => {
  let state = createScrubState(45, 180);
  let transition = transitionScrub(state, { type: 'input', position: 80, duration: 180 });
  state = transition.state;
  assert.deepEqual(state, { dragging: true, position: 80 });
  assert.equal(transition.seek, null);

  const polled = transitionScrub(state, { type: 'poll', position: 50, duration: 180 });
  assert.strictEqual(polled.state, state);
  assert.equal(polled.seek, null);

  transition = transitionScrub(state, { type: 'input', position: 95, duration: 180 });
  state = transition.state;
  transition = transitionScrub(state, { type: 'change', position: 95, duration: 180 });
  assert.deepEqual(transition, {
    state: { dragging: false, position: 95 }, seek: { position: 95 },
  });

  const followed = transitionScrub(transition.state,
    { type: 'poll', position: 96, duration: 180 });
  assert.deepEqual(followed.state, { dragging: false, position: 96 });
  assert.equal(followed.seek, null);
});

test('keyboard-style change emits a clamped absolute seek without an input transition', () => {
  const transition = transitionScrub(createScrubState(45, 180),
    { type: 'change', position: 181, duration: 180 });
  assert.deepEqual(transition, {
    state: { dragging: false, position: 180 }, seek: { position: 180 },
  });
});

test('local playback advancement is pure, bounded, and paused while dragging', () => {
  const state = createScrubState(179.8, 180);
  assert.deepEqual(transitionScrub(state,
    { type: 'advance', seconds: 0.25, duration: 180 }).state,
  { dragging: false, position: 180 });
  const dragging = { dragging: true, position: 90 };
  assert.strictEqual(transitionScrub(dragging,
    { type: 'advance', seconds: 1, duration: 180 }).state, dragging);
});
