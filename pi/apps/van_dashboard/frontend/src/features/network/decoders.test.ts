import { describe, expect, it } from 'vitest';

import {
  decodeConnectivityResponse,
  decodeOpenWrtClientsResponse,
  decodeSpeedtestResponse,
} from './schema';
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

  it('decodes clients and verifies the summary counts', () => {
    const status = decodeOpenWrtClientsResponse(clientsPayload());

    expect(status.clientCount).toBe(2);
    expect(status.clients[0]).toMatchObject({
      name: 'lion_fone',
      connection: 'wifi',
      band: '2.4 GHz',
    });
  });

  it('keeps speed-test measurements nullable until a run completes', () => {
    const running = decodeSpeedtestResponse(speedtestPayload('running'));
    const complete = decodeSpeedtestResponse(speedtestPayload('complete'));

    expect(running).toMatchObject({ status: 'running', downloadMbps: null });
    expect(complete).toMatchObject({ status: 'complete', downloadMbps: 42.5 });
  });
});
