import { EventEmitter } from 'node:events';
import { createConnection } from 'node:net';

export class MpvIpc extends EventEmitter {
  constructor(path, { timeoutMs = 5000 } = {}) {
    super();
    this.path = path;
    this.timeoutMs = timeoutMs;
    this.pending = new Map();
    this.sequence = 0;
    this.buffer = '';
  }

  connect() {
    return new Promise((resolve, reject) => {
      const socket = this.socket = createConnection(this.path);
      socket.setEncoding('utf8');
      const timer = setTimeout(() => socket.destroy(new Error('mpv connect timeout')), this.timeoutMs);
      socket.once('connect', () => { clearTimeout(timer); resolve(); });
      socket.on('error', error => { clearTimeout(timer); reject(error); });
      socket.on('data', chunk => this.receive(chunk));
      socket.on('close', () => {
        clearTimeout(timer);
        reject(new Error('mpv socket closed'));
        for (const request of this.pending.values()) request.reject(new Error('mpv disconnected'));
        this.pending.clear();
        this.emit('disconnect');
      });
    });
  }

  receive(chunk) {
    this.buffer += chunk;
    if (this.buffer.length > 1024 * 1024) {
      this.socket.destroy(new Error('mpv IPC frame too large'));
      return;
    }
    let newline;
    while ((newline = this.buffer.indexOf('\n')) !== -1) {
      const line = this.buffer.slice(0, newline);
      this.buffer = this.buffer.slice(newline + 1);
      let message;
      try { message = JSON.parse(line); } catch { continue; }
      if (message === null || typeof message !== 'object') continue;
      if (message.event) this.emit('event', message);
      const request = this.pending.get(message.request_id);
      if (!request) continue;
      this.pending.delete(message.request_id);
      if (message.error === 'success') request.resolve(message.data);
      else request.reject(new Error(`mpv: ${message.error || 'command failed'}`));
    }
  }

  command(command) {
    if (!this.socket || this.socket.destroyed) return Promise.reject(new Error('mpv is not connected'));
    return new Promise((resolve, reject) => {
      const id = ++this.sequence;
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error('mpv command timeout'));
      }, this.timeoutMs);
      const finish = callback => value => { clearTimeout(timer); callback(value); };
      this.pending.set(id, { resolve: finish(resolve), reject: finish(reject) });
      this.socket.write(`${JSON.stringify({ command, request_id: id })}\n`, error => {
        if (!error) return;
        this.pending.delete(id);
        clearTimeout(timer);
        reject(error);
      });
    });
  }

  close() { this.socket?.destroy(); }
}
