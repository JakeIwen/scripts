import { describe, expect, it, vi } from 'vitest';

import { startTelemetryVoltageCheck, toggleTelemetryService } from './api';

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('telemetry mutation API', () => {
  it('posts empty URL-encoded forms to the two narrow endpoints', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(
        jsonResponse({
          ok: true,
          message: 'Telemetry service stopped',
          service: { available: true, running: false },
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse(
          {
            ok: true,
            message: 'Voltage check started',
            check: { status: 'running' },
          },
          202,
        ),
      );

    await toggleTelemetryService();
    await startTelemetryVoltageCheck();

    for (const [path, init] of fetchMock.mock.calls) {
      expect(path).toMatch(/^\/api\/telemetry-/);
      expect(init).toMatchObject({
        cache: 'no-store',
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
          'X-Van-Dashboard': '1',
        },
      });
      expect(String(init?.body)).toBe('');
    }
  });
});
