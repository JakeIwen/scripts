import { describe, expect, it, vi } from 'vitest';

import { provisionUbntNetwork } from './api';
import { ubntPayload } from './testFixtures';
import type { UbntOperationKind, UbntProvisionRequest } from './types';

function startedResponse(kind: Exclude<UbntOperationKind, 'status'>): Response {
  const base = ubntPayload('running');
  const payload = {
    ...base,
    message: `UBNT ${kind} started`,
    operation: { ...base.operation, kind },
  };
  return new Response(JSON.stringify(payload), {
    status: 202,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('UBNT mutation API', () => {
  it('scrubs a password even when client-side validation rejects the request', async () => {
    const request: UbntProvisionRequest = {
      ssid: '../unsafe',
      security: 'wpa',
      bssid: '00:11:22:33:44:88',
      password: 'not-a-real-secret',
    };

    await expect(provisionUbntNetwork(request)).rejects.toThrow(
      'SSID cannot be safely stored as a UBNT profile',
    );
    expect(request.password).toBe('');
  });

  it('drops its password reference before the network response settles', async () => {
    let finishRequest!: (response: Response) => void;
    vi.spyOn(globalThis, 'fetch').mockReturnValue(
      new Promise<Response>((resolve) => {
        finishRequest = resolve;
      }),
    );
    const request: UbntProvisionRequest = {
      ssid: 'Camp Secure',
      security: 'wpa',
      bssid: '00:11:22:33:44:88',
      password: 'not-a-real-secret',
    };

    const pending = provisionUbntNetwork(request);
    expect(request.password).toBe('');
    finishRequest(startedResponse('provision'));
    await expect(pending).resolves.toMatchObject({ message: 'UBNT provision started' });
  });
});
