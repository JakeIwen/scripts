import { describe, expect, it, vi } from 'vitest';

import { setSonosGroupVolume } from './api';

describe('Sonos mutation API', () => {
  it('rejects an invalid volume before sending a request', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch');
    await expect(setSonosGroupVolume(101)).rejects.toThrow(
      'requested Sonos volume must be a whole number from 0 to 100',
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
