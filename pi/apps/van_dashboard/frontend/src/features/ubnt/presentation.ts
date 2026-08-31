import type { StatusTone } from '../../components/StatusPill';
import type { UbntOperation, UbntSecurity, UbntWifiStatus } from './types';

export function ubntSecurityLabel(security: UbntSecurity): string {
  if (security === 'wpa') return 'WPA/WPA2';
  if (security === 'none') return 'Open';
  if (security === 'enterprise') return 'WPA Enterprise';
  return 'WEP';
}

export function ubntTone(status: UbntWifiStatus | null, _error: Error | null): StatusTone {
  if (!status) return 'neutral';
  if (status.reachable === false) return 'bad';
  if (status.reachable === true) return 'good';
  return 'neutral';
}

export function ubntStatusLabel(status: UbntWifiStatus | null, _refreshing: boolean): string {
  if (!status) return 'No data';
  if (status.reachable === false) return 'Unavailable';
  if (status.reachable === true) return 'Connected';
  return 'No data';
}

export function ubntOperationLabel(operation: UbntOperation): string {
  if (operation.status === 'idle') return 'Idle';
  const kind =
    operation.kind === 'update-profile' ? 'profile update' : (operation.kind ?? 'status refresh');
  if (operation.status === 'running') return `${kind} running`;
  if (operation.status === 'error') return `${kind} failed`;
  return operation.message ?? `${kind} complete`;
}
