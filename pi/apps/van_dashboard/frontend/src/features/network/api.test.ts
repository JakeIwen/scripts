import { describe, expect, it, vi } from 'vitest';

import {
  fetchActiveConnectivity,
  fetchOpenWrtClients,
  fetchSpeedtestStatus,
  startSpeedtest,
} from './api';
import { clientsPayload, connectivityPayload, speedtestPayload } from './testFixtures';

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('network API paths', () => {
  it('uses the active connectivity lease and read-only status endpoints', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse(connectivityPayload()))
      .mockResolvedValueOnce(jsonResponse(clientsPayload()))
      .mockResolvedValueOnce(jsonResponse(speedtestPayload()))
      .mockResolvedValueOnce(
        jsonResponse({ ...speedtestPayload('running'), message: 'Speed test started' }),
      );
    const controller = new AbortController();

    await fetchActiveConnectivity(controller.signal);
    await fetchOpenWrtClients(controller.signal);
    await fetchSpeedtestStatus(controller.signal);
    await startSpeedtest();

    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/connectivity?active=1', {
      cache: 'no-store',
      signal: controller.signal,
    });
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/openwrt/clients', {
      cache: 'no-store',
      signal: controller.signal,
    });
    expect(fetchMock).toHaveBeenNthCalledWith(3, '/api/speedtest', {
      cache: 'no-store',
      signal: controller.signal,
    });
    const [startPath, startInit] = fetchMock.mock.calls[3]!;
    expect(startPath).toBe('/api/speedtest');
    expect(startInit).toMatchObject({
      cache: 'no-store',
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'X-Van-Dashboard': '1',
      },
    });
    expect(String(startInit?.body)).toBe('');
  });
});
