import { describe, expect, it } from 'vitest';

import { decodeUbntWifiStatus } from './decoder';
import { ubntPayload } from './testFixtures';

describe('decodeUbntWifiStatus', () => {
  it('strictly decodes state, profiles, networks, and operation', () => {
    const status = decodeUbntWifiStatus(ubntPayload());

    expect(status.state.associatedSsid).toBe('denlink');
    expect(status.state.snrDb).toBe(37);
    expect(status.profiles[0]?.hasPassword).toBe(true);
    expect(status.profiles[1]?.rateModule).toBe('ewma_ht');
    expect(status.networks[0]?.connected).toBe(true);
    expect(status.networks[1]?.security).toBe('enterprise');
    expect(status.operation.status).toBe('complete');
  });

  it('accepts the controller initial state before optional radio fields exist', () => {
    const payload = ubntPayload('idle');
    const initialState = payload.wifi.state as Record<string, unknown>;
    delete initialState.signal_dbm;
    delete initialState.noise_dbm;
    delete initialState.snr_db;
    const status = decodeUbntWifiStatus(payload);

    expect(status.state.signalDbm).toBeNull();
    expect(status.operation.kind).toBeNull();
  });

  it('rejects unknown security values rather than mislabeling a network', () => {
    const payload = ubntPayload();
    payload.wifi.networks[0]!.security = 'future-security';
    expect(() => decodeUbntWifiStatus(payload)).toThrow(
      'wifi.networks[0].security has an unsupported value: future-security',
    );
  });
});
