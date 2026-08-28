import { describe, expect, it } from 'vitest';

import {
  decodeConnectivityResponse,
  decodeOpenWrtClientsResponse,
  decodeSpeedtestResponse,
} from './decoders';
import { clientsPayload, connectivityPayload, speedtestPayload } from './testFixtures';

describe('network response decoders', () => {
  it('decodes connectivity and normalizes optional UBNT fields', () => {
    const status = decodeConnectivityResponse(connectivityPayload());

    expect(status.internetOnline).toBe(true);
    expect(status.router.routeMembers).toEqual([{ name: 'clientwan', percent: 100 }]);
    expect(status.router.interfaces[0]?.state).toBe('online');
    expect(status.ubnt.accessPoint).toBeNull();
    expect(status.ubnt.frequencyGhz).toBeNull();
  });

  it('rejects an unknown MWAN3 interface state at the API boundary', () => {
    const payload = connectivityPayload();
    const connectivity = payload.connectivity as Record<string, unknown>;
    const router = connectivity.router as Record<string, unknown>;
    const interfaces = router.interfaces as Record<string, unknown>[];
    if (interfaces[0]) interfaces[0].state = 'mysterious';

    expect(() => decodeConnectivityResponse(payload)).toThrow(
      'connectivity.router.interfaces[0].state has an unsupported value',
    );
  });

  it('decodes clients and verifies the summary counts', () => {
    const status = decodeOpenWrtClientsResponse(clientsPayload());

    expect(status.clientCount).toBe(2);
    expect(status.clients[0]).toMatchObject({
      name: 'lion_fone',
      connection: 'wifi',
      band: '2.4 GHz',
    });
  });

  it('rejects client counts that disagree with the returned devices', () => {
    const payload = clientsPayload();
    const openwrt = payload.openwrt as Record<string, unknown>;
    openwrt.wifi_count = 2;

    expect(() => decodeOpenWrtClientsResponse(payload)).toThrow(
      'openwrt client counts do not match openwrt.clients',
    );
  });

  it('keeps speed-test measurements nullable until a run completes', () => {
    const running = decodeSpeedtestResponse(speedtestPayload('running'));
    const complete = decodeSpeedtestResponse(speedtestPayload('complete'));

    expect(running).toMatchObject({ status: 'running', downloadMbps: null });
    expect(complete).toMatchObject({ status: 'complete', downloadMbps: 42.5 });
  });
});
