import { Constants } from 'yt-cast-receiver';
import { randomUUID } from 'node:crypto';
import { config } from './config.mjs';
import { PairingCodes } from './pairing-codes.mjs';
import { listenStatus } from './status-server.mjs';
import { PlayerStatus } from './mpv-player.mjs';

export async function runReceiver({ receiver, player, store, log = console }) {
  const instance = randomUUID();
  const pairing = new PairingCodes(receiver.getPairingCodeRequestService(), { log });
  let stopping = false;
  let closeStatus;
  let stopPromise;
  const snapshot = () => ({
    ok: !stopping && receiver.status === Constants.STATUSES.RUNNING,
    name: config.name, pid: process.pid, instance, receiver: receiver.status,
    player: Object.keys(PlayerStatus).find(key => PlayerStatus[key] === player.status) || 'UNKNOWN',
    video: player.video, position: player.position, duration: player.duration,
    senders: receiver.getConnectedSenders().length,
  });
  const stop = (code = 0) => {
    if (stopPromise) return stopPromise;
    stopping = true;
    stopPromise = (async () => {
      const deadline = setTimeout(() => process.exit(code || 1), 18_000);
      try {
        pairing.stop();
        await player.endSession();
        await receiver.stop();
        await closeStatus?.();
        await store.writes;
      } catch { code = 1; log.error('Receiver shutdown failed'); }
      clearTimeout(deadline);
      process.exit(code);
    })();
    return stopPromise;
  };
  process.once('SIGTERM', () => void stop());
  process.once('SIGINT', () => void stop());
  receiver.on('error', () => log.warn('Receiver connection error'));
  receiver.on('terminate', () => { log.error('Receiver terminated; supervisor will retry'); void stop(1); });
  receiver.on('senderDisconnect', (_sender, implicit) => {
    if (!implicit && receiver.getConnectedSenders().length === 0) {
      void player.endSession().catch(() => log.warn('Player disconnect cleanup failed'));
    }
  });
  try {
    await store.initialize();
    await player.initialize();
    // Stop also clears the queue: the library replays a cast only when its
    // current video changes, so recasting the stopped video would do nothing.
    const controls = { pause: () => player.pause(), resume: () => player.resume(),
      stop: async () => { await player.reset(); return true; } };
    const cast = async (videoId, position) => {
      if (receiver.status !== Constants.STATUSES.RUNNING) throw new Error('Receiver not running');
      void player.play({ id: videoId, client: Constants.CLIENTS.YT }, position)
        .catch(() => log.warn('Direct cast failed'));
    };
    closeStatus = await listenStatus({ snapshot, pairing, cast, control: action => controls[action]() });
    const startup = setTimeout(() => { log.error('Receiver startup timeout'); void stop(1); }, 60_000);
    try { await receiver.start(); } finally { clearTimeout(startup); }
    if (!stopping) pairing.start();
    log.info(`Van TV ready; status port ${config.statusPort}, DIAL port ${config.dialPort}`);
  } catch { log.error('Receiver startup failed'); await stop(1); }
}
