import { afterEach, describe, expect, it, vi } from 'vitest';

import { getJson, postForm } from './client';

describe('dashboard API client', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('sends URL-encoded mutations with the dashboard CSRF header', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true, message: 'updated' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    await postForm('/api/example', { enabled: 'true', level: 12 });

    expect(fetchMock).toHaveBeenCalledOnce();
    const [path, request] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(path).toBe('/api/example');
    expect(request.method).toBe('POST');
    expect(request.cache).toBe('no-store');
    expect(request.headers).toEqual({
      'Content-Type': 'application/x-www-form-urlencoded',
      'X-Van-Dashboard': '1',
    });
    expect(String(request.body)).toBe('enabled=true&level=12');
  });

  it('turns non-success responses into a useful typed error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ ok: false, message: 'not allowed' }), {
          status: 403,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    );

    await expect(getJson('/api/status')).rejects.toMatchObject({
      name: 'ApiRequestError',
      message: 'not allowed',
      status: 403,
    });
  });

  it('refuses non-API paths', async () => {
    await expect(getJson('/status')).rejects.toThrow('must start with /api/');
  });
});
