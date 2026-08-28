import { describe, expect, it, vi } from 'vitest';

import { requestDashboardRestart, requestSystemPower } from './api';

function response(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 202,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('system-control mutation forms', () => {
  it('sends exact confirmed URL-encoded forms', async () => {
    const operation = {
      status: 'running',
      action: 'reboot',
      started_at: 1_700_000_000,
      completed_at: null,
      error: null,
    };
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(response({ ok: true, message: 'rebooting', system_power: operation }))
      .mockResolvedValueOnce(
        response({
          ok: true,
          message: 'powering down',
          system_power: { ...operation, action: 'power-down' },
        }),
      )
      .mockResolvedValueOnce(
        response({
          ok: true,
          message: 'restart scheduled',
          dashboard_restart: { scheduled_at: 1_700_000_001 },
        }),
      );

    await requestSystemPower('reboot');
    await requestSystemPower('power-down');
    await requestDashboardRestart();

    expect(
      fetchMock.mock.calls.map(([path, options]) => [
        path,
        (options?.body as URLSearchParams).toString(),
      ]),
    ).toEqual([
      ['/api/system-power', 'action=reboot&confirmation=reboot'],
      ['/api/system-power', 'action=power-down&confirmation=power-down'],
      ['/api/dashboard-service/restart', 'confirmation=restart-dashboard'],
    ]);
  });
});
