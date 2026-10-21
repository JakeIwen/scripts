import { EventEmitter } from 'node:events';
import YouTubeCastReceiver, { Constants, PlaylistRequestHandler } from 'yt-cast-receiver';
import { MpvPlayer } from '../src/mpv-player.mjs';

export const VIDEO = { id: 'abcdefghijk', client: Constants.CLIENTS.YT, context: { playlistId: 'queue', index: 0 } };
export const SECOND = { id: '0123456789a', client: Constants.CLIENTS.YT, context: { playlistId: 'queue', index: 1 } };
export const quiet = { warn() {}, error() {} };

export class FakeMpv extends EventEmitter {
  constructor() { super(); this.commands = []; this.closed = false; }
  async start(signal) { signal.throwIfAborted(); }
  async command(command) { this.commands.push(command); }
  async load(id, position, signal) { signal.throwIfAborted(); this.loaded = { id, position }; }
  async close() { this.closed = true; this.emit('disconnect'); }
  property(name, data) { this.emit('event', { event: 'property-change', name, data }); }
}

class QueueNavigation extends PlaylistRequestHandler {
  async getPreviousNextVideos(video) { return video.id === VIDEO.id ? { next: SECOND } : {}; }
}

export function playerFixture(options = {}) {
  const instances = [];
  const events = [];
  let prepared = 0;
  let ended = 0;
  const player = new MpvPlayer({
    createMpv: () => { const instance = new FakeMpv(); instances.push(instance); return instance; },
    room: { beforeVideo: async () => { prepared++; }, endSession: () => { ended++; } },
    handoff: async () => {}, segments: async () => [], log: quiet,
    ...options,
  });
  // Construction wires the library's real state/queue implementation without starting networks.
  const receiver = new YouTubeCastReceiver(player, { dataStore: false,
    logLevel: Constants.LOG_LEVELS.NONE,
    app: { playlistRequestHandler: new QueueNavigation() },
  });
  player.on('state', event => events.push(event.current.status));
  return { player, receiver, instances, events, prepared: () => prepared, ended: () => ended };
}

export async function settleUntil(predicate) {
  for (let attempt = 0; attempt < 50; attempt++) {
    if (predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 2));
  }
  throw new Error('Expected state was not reached');
}
