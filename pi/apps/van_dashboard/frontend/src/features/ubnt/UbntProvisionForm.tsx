import { useId, useRef } from 'react';

import { ubntSecurityLabel } from './presentation';
import type { UbntNetwork, UbntProvisionRequest } from './types';

export interface UbntProvisionFormProps {
  network: UbntNetwork;
  disabled: boolean;
  onCancel: () => void;
  onSubmit: (request: UbntProvisionRequest) => Promise<boolean>;
}

export function UbntProvisionForm({
  network,
  disabled,
  onCancel,
  onSubmit,
}: UbntProvisionFormProps) {
  const passwordId = useId();
  const passwordRef = useRef<HTMLInputElement>(null);

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const password = passwordRef.current?.value ?? '';
    if (passwordRef.current) passwordRef.current.value = '';

    const saved = await onSubmit({
      ssid: network.ssid,
      security: 'wpa',
      bssid: network.bssid,
      password,
    });
    if (saved) onCancel();
  };

  return (
    <form className="ubnt-provision-form" onSubmit={(event) => void submit(event)}>
      <header>
        <span>
          <strong>Join {network.ssid}</strong>
          <small>
            {ubntSecurityLabel(network.security)} · {network.bssid}
          </small>
        </span>
        <button type="button" className="icon-button" onClick={onCancel} aria-label="Cancel join">
          ×
        </button>
      </header>
      <label htmlFor={passwordId}>Wi-Fi password</label>
      <input
        id={passwordId}
        ref={passwordRef}
        name="password"
        type="password"
        minLength={8}
        maxLength={63}
        autoComplete="current-password"
        autoFocus
        disabled={disabled}
        required
      />
      <small>
        Sent directly to the existing controller and cleared from this form immediately.
      </small>
      <div>
        <button type="button" className="secondary-button" disabled={disabled} onClick={onCancel}>
          Cancel
        </button>
        <button type="submit" disabled={disabled}>
          Save &amp; connect
        </button>
      </div>
    </form>
  );
}
