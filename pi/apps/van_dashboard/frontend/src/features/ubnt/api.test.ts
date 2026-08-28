import { beforeEach, describe, expect, it, vi } from 'vitest';

import { getJson } from '../../api/client';
import { fetchUbntWifiStatus } from './api';
import { ubntPayload } from './testFixtures';

vi.mock('../../api/client', () => ({ getJson: vi.fn(), postForm: vi.fn() }));

describe('fetchUbntWifiStatus', () => {
  beforeEach(() => {
    vi.mocked(getJson).mockResolvedValue(ubntPayload());
  });

  it('uses the single GET-only UBNT status endpoint', async () => {
    const controller = new AbortController();
    const status = await fetchUbntWifiStatus(controller.signal);

    expect(getJson).toHaveBeenCalledWith('/api/ubnt-wifi', controller.signal);
    expect(status.profiles).toHaveLength(2);
  });
});
