import test from 'node:test';
import assert from 'node:assert/strict';
import { PlayerStatus } from '../src/mpv-player.mjs';
import { playerFixture, FakeMpv, VIDEO, SECOND, settleUntil } from './player-fixture.mjs';

test('Public player API reports loading/play/pause/resume/seek/volume/stop', async t => {
  const { player, instances, events } = playerFixture();
  t.after(() => player.endSession());
  assert.equal(await player.play(VIDEO, 12), true);
  assert.deepEqual(events.slice(0, 2), [PlayerStatus.LOADING, PlayerStatus.PLAYING]);
  assert.deepEqual(instances[0].loaded, { id: VIDEO.id, position: 12 });
  instances[0].property('duration', 120);
  instances[0].property('time-pos', 15);
  assert.equal(await player.getDuration(), 120);
  assert.equal(await player.getPosition(), 15);
  assert.equal(await player.pause(), true);
  assert.equal(player.status, PlayerStatus.PAUSED);
  assert.equal(await player.resume(), true);
  assert.equal(await player.seek(40), true);
  assert.equal(await player.getPosition(), 40);
  assert.equal(await player.setVolume({ level: 34, muted: true }), true);
  assert.deepEqual(await player.getVolume(), { level: 34, muted: true });
  await player.stop();
  assert.equal(player.status, PlayerStatus.STOPPED);
  assert.equal(instances[0].closed, true);
  assert.equal(player.video, null);
  assert.ok(instances[0].commands.some(command => command[0] === 'seek' && command[1] === 40));
});

test('Natural EOF advances the real receiver queue, then closes mpv at queue end', async t => {
  const { player, instances } = playerFixture();
  t.after(() => player.endSession());
  await player.queue.updateByMessage({ name: 'setPlaylist', payload: {
    listId: 'queue', videoIds: `${VIDEO.id},${SECOND.id}`, videoId: VIDEO.id, currentIndex: '0',
  } }, VIDEO.client);
  await player.play(VIDEO);
  await settleUntil(() => !player.queue.isUpdating);
  instances[0].emit('event', { event: 'end-file', reason: 'eof' });
  await settleUntil(() => player.video?.id === SECOND.id && player.status === PlayerStatus.PLAYING);
  assert.equal(instances[0].closed, true);
  await settleUntil(() => !player.queue.isUpdating);
  instances[1].emit('event', { event: 'end-file', reason: 'eof' });
  await settleUntil(() => player.status === PlayerStatus.STOPPED);
  assert.equal(instances[1].closed, true);
});

test('External mpv pause/volume and SponsorBlock seeks reach phone state', async t => {
  const { player, instances } = playerFixture({ segments: async () => [[10, 20]] });
  t.after(() => player.endSession());
  await player.play(VIDEO);
  instances[0].property('time-pos', 12);
  await settleUntil(() => player.position === 20);
  instances[0].property('pause', true);
  await settleUntil(() => player.status === PlayerStatus.PAUSED);
  instances[0].property('volume', 23);
  assert.equal((await player.getVolume()).level, 23);
});

test('Stop cancels a pending load, closes the process and never reports playing', async () => {
  let loading;
  const started = new Promise(resolve => { loading = resolve; });
  class SlowMpv extends FakeMpv {
    async load(_id, _position, signal) {
      loading();
      return new Promise((resolve, reject) => signal.addEventListener('abort', () => reject(new Error('cancelled')), { once: true }));
    }
  }
  const mpv = new SlowMpv();
  const { player, events } = playerFixture({ createMpv: () => mpv });
  const play = player.play(VIDEO);
  await started;
  await player.stop();
  assert.equal(await play, false);
  assert.equal(mpv.closed, true);
  assert.equal(events.includes(PlayerStatus.PLAYING), false);
});

test('Rapid replacement cancels old preparation; old completion cannot overwrite new state', async t => {
  let preparing;
  const started = new Promise(resolve => { preparing = resolve; });
  let first = true;
  const { player } = playerFixture({ handoff: async ({ signal }) => {
    if (!first) return;
    first = false;
    preparing();
    await new Promise((resolve, reject) => signal.addEventListener('abort', () => reject(new Error('cancelled')), { once: true }));
  } });
  t.after(() => player.endSession());
  const old = player.play(VIDEO);
  await started;
  const replacement = player.play(SECOND);
  assert.equal(await old, false);
  assert.equal(await replacement, true);
  assert.equal(player.status, PlayerStatus.PLAYING);
  assert.equal(player.video.id, SECOND.id);
});

test('Persisted volume survives mpv initial property observations', async t => {
  class ObservedMpv extends FakeMpv {
    async command(command) {
      await super.command(command);
      if (command[0] === 'observe_property' && command[2] === 'volume') this.property('volume', 100);
      if (command[0] === 'observe_property' && command[2] === 'mute') this.property('mute', false);
    }
  }
  const mpv = new ObservedMpv();
  const { player } = playerFixture({ createMpv: () => mpv,
    store: { get: async () => ({ volume: { level: 24, muted: true } }) },
  });
  t.after(() => player.endSession());
  await player.initialize();
  await player.play(VIDEO);
  assert.deepEqual(await player.getVolume(), { level: 24, muted: true });
  assert.ok(mpv.commands.some(command => command[1] === 'volume' && command[2] === 24));
});

test('Playback errors and bad video IDs leave no player window', async () => {
  const mpv = new FakeMpv();
  mpv.load = async () => { throw new Error('yt-dlp failed'); };
  const { player, events } = playerFixture({ createMpv: () => mpv });
  assert.equal(await player.play({ id: '--unsafe' }), false);
  assert.equal(await player.play(VIDEO), false);
  assert.equal(mpv.closed, true);
  assert.equal(player.status, PlayerStatus.STOPPED);
  assert.equal(events.includes(PlayerStatus.PLAYING), false);
  await player.endSession();
});
