import type { Consumer, ConsumerState, HostState, ModeValue, PolicyEdit } from './schema';

export const MAX_MODE_HOURS = 24;
const STATE_FRESH_SECONDS = 30;

export function isGated(consumer: Consumer): boolean {
  return consumer.how !== 'unprotected' && consumer.how !== 'advisory';
}

export function timeLabel(unixSeconds: number): string {
  return new Date(unixSeconds * 1000).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

const approvalLabels = {
  until: (state: ConsumerState) => `Permitted until ${timeLabel(state.approval!.until!)}`,
  episode: () => 'Permitted for this metered episode',
};
const actualLabels: Record<ConsumerState['actual'], string> = {
  running: 'Running',
  held: 'Held',
  stopped_manually: 'Stopped manually',
  unknown: 'Unknown',
  'n/a': 'Not applicable',
};
const handlingLabels: Record<Consumer['how'], string | null> = {
  tick: null,
  guard: null,
  run: null,
  external: null,
  unprotected: 'Unprotected',
  advisory: 'Advisory',
};

export function permissionLabel(state: ConsumerState | undefined, host: HostState): string {
  if (!state) return 'Unknown';
  if (!state.permitted) return 'Held';
  if (state.actual === 'stopped_manually') return 'Permitted, but stopped manually';
  if (state.reason === 'not gated (explicit exemption)') return 'Permitted (not gated)';
  if (!host.effective.metered) return 'Permitted (unmetered)';
  if (state.approval) return approvalLabels[state.approval.kind](state);
  return 'Permitted';
}

export function consumerLabels(consumer: Consumer, host: HostState, stale = false) {
  const state = host.consumers[consumer.id];
  return {
    permission: stale ? 'Unknown' : permissionLabel(state, host),
    actual:
      handlingLabels[consumer.how] ??
      (stale ? 'Unknown' : actualLabels[state?.actual ?? 'unknown']),
  };
}

export function stateIsStale(state: HostState, now = Date.now() / 1000): boolean {
  return (
    state.evaluated_at > now ||
    now - state.evaluated_at > STATE_FRESH_SECONDS ||
    state.valid_until === null ||
    now >= state.valid_until
  );
}

export function modeEdit(value: ModeValue, hours: string): PolicyEdit {
  if (value === 'auto') return { op: 'mode', value };
  if (hours === '' && value === 'metered') return { op: 'mode', value };
  const duration = Number(hours);
  if (!Number.isFinite(duration) || duration <= 0 || duration > MAX_MODE_HOURS) {
    throw new Error('Choose a duration greater than 0 and at most 24 hours.');
  }
  return { op: 'mode', value, seconds: duration * 3600 };
}

export function ssidError(ssid: string): string | null {
  const bytes = new TextEncoder().encode(ssid).length;
  if (bytes < 1 || bytes > 32) return 'SSID must contain 1 to 32 UTF-8 bytes.';
  if (/[\u0000-\u001f\u007f]/.test(ssid)) return 'SSID cannot contain control characters.';
  return null;
}
