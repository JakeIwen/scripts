import { EventEmitter } from 'node:events';
import { spawn } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { config, playback, mpvArguments } from './config.mjs';
import { MpvIpc } from './mpv-ipc.mjs';
import { signalGroup } from './process-command.mjs';

export class MpvProcess extends EventEmitter {
  async start(signal) {
    signal?.throwIfAborted();
    this.directory = await mkdtemp(join(config.runtimeDirectory, 'mpv-'));
    if (this.closing || signal?.aborted) {
      await rm(this.directory, { recursive: true, force: true });
      throw new Error('Playback cancelled');
    }
    const socket = join(this.directory, 'ipc');
    this.child = spawn(playback.mpv, mpvArguments(socket), {
      detached: true, stdio: 'ignore',
      env: { ...process.env, ...config.sessionEnvironment },
    });
    this.child.once('error', error => { this.failure = error; });
    this.exit = new Promise(resolve => {
      this.child.once('exit', resolve);
      this.child.once('error', resolve);
    });
    const deadline = Date.now() + 5000;
    while (Date.now() < deadline) {
      signal?.throwIfAborted();
      if (this.closing || this.failure || this.child.exitCode !== null) throw new Error('mpv failed to start');
      const ipc = new MpvIpc(socket);
      try {
        await ipc.connect();
        this.ipc = ipc;
        ipc.on('event', message => this.emit('event', message));
        ipc.on('disconnect', () => { if (!this.closing) this.emit('disconnect'); });
        return;
      } catch {
        ipc.close();
        await delay(50, undefined, { signal });
      }
    }
    throw new Error('mpv IPC startup timeout');
  }

  command(argv) { return this.ipc.command(argv); }

  async load(videoId, position, signal) {
    const loaded = this.waitForLoad(signal);
    // Attach rejection handling before sending: an IPC failure can beat the ACK.
    loaded.catch(() => {});
    try {
      await this.command(['loadfile', `https://www.youtube.com/watch?v=${videoId}`,
        'replace', `start=${position}`]);
      await loaded;
    } catch (error) {
      await this.close();
      throw error;
    }
  }

  waitForLoad(signal) {
    return new Promise((resolve, reject) => {
      const finish = error => {
        clearTimeout(timer);
        this.removeListener('event', event);
        this.removeListener('disconnect', disconnect);
        signal?.removeEventListener('abort', cancel);
        if (error) reject(error); else resolve();
      };
      const event = message => {
        if (message.event === 'file-loaded') finish();
        else if (message.event === 'end-file' && message.reason === 'error') finish(new Error('mpv could not load video'));
      };
      const disconnect = () => finish(new Error('mpv disconnected during load'));
      const cancel = () => finish(new Error('Playback cancelled'));
      const timer = setTimeout(() => finish(new Error('Video load timeout')), config.loadTimeoutMs);
      this.on('event', event);
      this.on('disconnect', disconnect);
      signal?.addEventListener('abort', cancel, { once: true });
      if (signal?.aborted) cancel();
    });
  }

  close() {
    this.closing = true;
    return this.closePromise ||= this.closeProcess();
  }

  async closeProcess() {
    if (this.child) {
      await Promise.race([
        this.ipc?.command(['quit']).catch(() => {}), delay(500),
      ]);
      signalGroup(this.child.pid, 'SIGTERM');
      await Promise.race([this.exit, delay(500)]);
      signalGroup(this.child.pid, 'SIGKILL');
      await this.exit;
    }
    this.emit('disconnect');
    this.ipc?.close();
    if (this.directory) await rm(this.directory, { recursive: true, force: true });
  }
}
