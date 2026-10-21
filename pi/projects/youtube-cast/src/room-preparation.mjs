import { config } from './config.mjs';
import { runCommand } from './process-command.mjs';

export class RoomPreparation {
  constructor({ run = runCommand, log = console } = {}) {
    this.run = run;
    this.log = log;
    this.prepared = false;
    this.pending = null;
  }

  async beforeVideo(signal) {
    const wake = this.run(config.wakeCommand, { signal }).catch(() => this.log.warn('Screen wake failed'));
    if (!this.prepared && !this.pending) {
      this.pending = this.run(config.roomCommand, { timeoutMs: config.roomTimeoutMs, signal })
        .then(() => { this.prepared = true; })
        .catch(() => this.log.warn('Rear Sonos preparation failed; continuing playback'))
        .finally(() => { this.pending = null; });
    }
    await Promise.all([wake, this.pending]);
    signal?.throwIfAborted();
  }

  endSession() { this.prepared = false; }
}
