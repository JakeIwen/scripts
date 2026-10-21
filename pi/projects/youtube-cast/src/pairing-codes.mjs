import { config } from './config.mjs';

export class PairingCodes {
  constructor(service, { log = console, now = Date.now } = {}) {
    Object.assign(this, { service, log, now });
    this.code = null;
    this.expiresAt = 0;
    this.requesting = false;
    this.error = null;
    this.closed = false;
    service.on('request', () => { this.requesting = true; this.error = null; });
    service.on('response', code => this.received(code));
    service.on('error', () => this.failed());
  }

  received(raw) {
    if (this.closed) { queueMicrotask(() => this.service.stop()); return; }
    const code = String(raw).replace(/\s/g, '');
    if (!/^\d{12}$/.test(code)) { this.failed(); return; }
    this.code = code.replace(/(.{3})(?=.)/g, '$1 ');
    this.expiresAt = this.now() + config.pairingLifetimeMs;
    this.requesting = false;
    this.error = null;
  }

  failed() {
    this.requesting = false;
    this.code = null;
    this.error = 'Pairing code unavailable; retrying';
    this.log.warn('Pairing code request failed');
    // The library schedules its next refresh after emitting response, even if invalid.
    queueMicrotask(() => this.service.stop());
    clearTimeout(this.retry);
    if (!this.closed) this.retry = setTimeout(() => this.refresh(), config.pairingRetryMs);
  }

  snapshot() {
    return { code: this.now() < this.expiresAt ? this.code : null,
      expiresAt: this.expiresAt, requesting: this.requesting, error: this.error };
  }

  refresh() {
    if (this.closed || this.requesting) return false;
    clearTimeout(this.retry);
    this.code = null;
    this.error = null;
    this.service.stop();
    this.service.start();
    return true;
  }

  start() { this.service.start(); }
  stop() {
    this.closed = true;
    clearTimeout(this.retry);
    this.service.stop();
  }
}
