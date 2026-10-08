import type { StatusTone } from '../../components/StatusPill';
import type { UbntOperation, UbntSecurity, UbntWifiStatus } from './types';

export function ubntSecurityLabel(security: UbntSecurity): string {
  if (security === 'wpa') return 'WPA/WPA2';
  if (security === 'none') return 'Open';
  if (security === 'enterprise') return 'WPA Enterprise';
  return 'WEP';
}

export function ubntRadioConnected(status: UbntWifiStatus | null): boolean {
  return (
    status?.reachable === true &&
    !status.lastError &&
    Boolean(status.state.associatedSsid) &&
    (status.state.ccqPercent ?? 0) > 0
  );
}

export function ubntTone(status: UbntWifiStatus | null, error: Error | null): StatusTone {
  if (ubntRecovering(status) || status?.operation.status === 'running') return 'neutral';
  if (error || status?.lastError || status?.starlinkPending) return 'neutral';
  if (!status) return 'neutral';
  if (status.reachable === false) return 'bad';
  if (ubntRadioConnected(status)) return 'good';
  return 'neutral';
}

export function ubntStatusLabel(status: UbntWifiStatus | null, error: Error | null): string {
  if (ubntRecovering(status)) return 'Reconnecting';
  if (status?.operation.status === 'running') return 'Working';
  if (status?.starlinkPending) return 'Queued';
  if (error || status?.lastError) return 'Status unavailable';
  if (!status) return 'No data';
  if (status.reachable === false) return 'Unavailable';
  if (ubntRadioConnected(status)) return 'Connected';
  if (status.state.selectorRunning) return 'Reconnecting';
  if (status.reachable === true) return 'Not associated';
  return 'No data';
}

export function ubntOperationLabel(operation: UbntOperation): string {
  if (operation.status === 'idle') return 'Idle';
  const kind =
    operation.kind === 'update-profile' ? 'profile update' : (operation.kind ?? 'status refresh');
  if (operation.status === 'running') return operation.message ?? `${kind} running`;
  if (operation.status === 'error') return `${kind} failed`;
  return operation.message ?? `${kind} complete`;
}

export function ubntRecovering(status: UbntWifiStatus | null): boolean {
  return Boolean(
    status?.operation.status === 'running' &&
    ['connect', 'provision', 'update-profile', 'forget', 'starlink', 'scan', 'abort'].includes(
      status.operation.kind ?? '',
    ) &&
    status.lastError &&
    /connection|timed out|broken pipe|permission denied/i.test(status.lastError),
  );
}
