import { describe, expect, it, vi } from 'vitest';

import {
  controlSonosTransport,
  selectSonosCoordinator,
  setSonosGrouping,
  setSonosGroupMuted,
  setSonosGroupVolume,
  setSonosSpeakerMuted,
  setSonosSpeakerVolume,
} from './api';

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('Sonos mutation API', () => {
  it('sends only the exact URL-encoded form for every control', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse({ ok: true, message: 'using Rear', device: 'Rear' }))
      .mockResolvedValueOnce(jsonResponse({ ok: true, message: 'added Solo' }))
      .mockResolvedValueOnce(jsonResponse({ ok: true, message: 'volume 37', volume: 37 }))
      .mockResolvedValueOnce(jsonResponse({ ok: true, message: 'muted', muted: true }))
      .mockResolvedValueOnce(jsonResponse({ ok: true, message: 'group 64', volume: 64 }))
      .mockResolvedValueOnce(jsonResponse({ ok: true, message: 'group unmuted', muted: false }))
      .mockResolvedValueOnce(jsonResponse({ ok: true, message: 'next track' }));

    await selectSonosCoordinator('Rear');
    await setSonosGrouping('Solo', true);
    await setSonosSpeakerVolume('Rear', 37);
    await setSonosSpeakerMuted('Rear', true);
    await setSonosGroupVolume(64);
    await setSonosGroupMuted(false);
    await controlSonosTransport('next');

    expect(
      fetchMock.mock.calls.map(([path, options]) => [
        path,
        (options?.body as URLSearchParams).toString(),
      ]),
    ).toEqual([
      ['/api/speakers/select', 'name=Rear'],
      ['/api/speakers/group', 'name=Solo&grouped=1'],
      ['/api/speakers/volume', 'name=Rear&volume=37'],
      ['/api/speakers/mute', 'name=Rear&muted=1'],
      ['/api/speakers/group-volume', 'volume=64'],
      ['/api/speakers/group-mute', 'muted=0'],
      ['/api/speakers/transport', 'action=next'],
    ]);
    for (const [, options] of fetchMock.mock.calls) {
      expect(options).toMatchObject({
        method: 'POST',
        cache: 'no-store',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
          'X-Van-Dashboard': '1',
        },
      });
    }
  });

  it('rejects an invalid volume before sending a request', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch');
    await expect(setSonosGroupVolume(101)).rejects.toThrow(
      'requested Sonos volume must be a whole number from 0 to 100',
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
