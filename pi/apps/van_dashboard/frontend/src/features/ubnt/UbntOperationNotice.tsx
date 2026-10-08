import { ubntOperationLabel } from './presentation';
import type { UbntOperation } from './types';

export function UbntOperationNotice({ operation }: { operation: UbntOperation | undefined }) {
  if (!operation || operation.status === 'idle') return null;
  const pending = operation.status === 'error' && operation.confirmationPending;
  return (
    <section className={`ubnt-operation ubnt-operation--${pending ? 'pending' : operation.status}`}>
      <strong>Latest antenna operation</strong>
      <span>{ubntOperationLabel(operation)}</span>
      {pending ? (
        <small>
          The antenna has not confirmed the Wi-Fi change yet. It may still be switching networks;
          current connection status will continue to update.
        </small>
      ) : operation.error ? (
        <small>{operation.error}</small>
      ) : null}
    </section>
  );
}
