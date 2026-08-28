import { describe, expect, it, vi } from 'vitest';

import {
  connectUbntProfile,
  provisionUbntNetwork,
  resumeUbntAutomaticSelection,
  scanUbntNetworks,
  updateUbntProfile,
} from './api';
import { ubntPayload } from './testFixtures';
import type { UbntOperationKind, UbntProfileUpdate, UbntProvisionRequest } from './types';

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
  it('sends each endpoint its exact URL-encoded form and returns no secret fields', async () => {
    const kinds: Exclude<UbntOperationKind, 'status'>[] = [
      'scan',
      'connect',
      'provision',
      'provision',
      'resume',
      'update-profile',
    ];
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => {
      const kind = kinds.shift();
      if (!kind) throw new Error('unexpected UBNT request');
      return startedResponse(kind);
    });
    const wpa: UbntProvisionRequest = {
      ssid: 'Camp Secure',
      security: 'wpa',
      bssid: '00:11:22:33:44:88',
      password: 'not-a-real-secret',
    };
    const open: UbntProvisionRequest = {
      ssid: 'Park Guest',
      security: 'none',
      bssid: '00:11:22:33:44:99',
      password: '',
    };
    const profile: UbntProfileUpdate = {
      profile: 'Camp Guest',
      password: 'replacement-secret',
      bssid: 'aa:bb:cc:dd:ee:ff',
      outputPowerDbm: 19,
      rateModule: 'ewma_ht',
      rateAuto: false,
      rateMcs: 7,
      applyNow: true,
    };

    const results = [
      await scanUbntNetworks(),
      await connectUbntProfile('Camp Guest'),
      await provisionUbntNetwork(wpa),
      await provisionUbntNetwork(open),
      await resumeUbntAutomaticSelection(),
      await updateUbntProfile(profile),
    ];

    const expected = [
      ['/api/ubnt-wifi/scan', ''],
      ['/api/ubnt-wifi/connect', 'profile=Camp+Guest'],
      [
        '/api/ubnt-wifi/provision',
        'ssid=Camp+Secure&security=wpa&bssid=00%3A11%3A22%3A33%3A44%3A88&password=not-a-real-secret',
      ],
      [
        '/api/ubnt-wifi/provision',
        'ssid=Park+Guest&security=none&bssid=00%3A11%3A22%3A33%3A44%3A99&password=',
      ],
      ['/api/ubnt-wifi/resume', ''],
      [
        '/api/ubnt-wifi/profile',
        'profile=Camp+Guest&password=replacement-secret&bssid=AA%3ABB%3ACC%3ADD%3AEE%3AFF&output_power_dbm=19&rate_module=ewma_ht&rate_auto=false&rate_mcs=7&apply_now=true',
      ],
    ] as const;

    expected.forEach(([path, body], index) => {
      const [actualPath, init] = fetchMock.mock.calls[index]!;
      expect(actualPath).toBe(path);
      expect(init).toMatchObject({
        cache: 'no-store',
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
          'X-Van-Dashboard': '1',
        },
      });
      expect(String(init?.body)).toBe(body);
    });
    expect(wpa.password).toBe('');
    expect(profile.password).toBe('');
    expect(JSON.stringify(results)).not.toContain('not-a-real-secret');
    expect(JSON.stringify(results)).not.toContain('replacement-secret');
  });

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
