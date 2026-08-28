import { describe, expect, it, vi } from 'vitest';

import { postForm } from '../../api/client';
import { setCopAlert, toggleStarlinkPower } from './api';

vi.mock('../../api/client', () => ({ postForm: vi.fn() }));

describe('status mutations', () => {
  it('sends an explicit desired COP ALERT state', async () => {
    vi.mocked(postForm).mockResolvedValue({
      ok: true,
      message: 'COP ALERT armed',
      cop_alert: {
        active: true,
        ignition_on: false,
        ext_flood: 'on',
        last_ntfy: null,
        last_error: null,
      },
    });
    await setCopAlert(true);
    expect(postForm).toHaveBeenCalledWith('/api/cop-alert', { active: 'true' });
  });

  it('serializes an explicit false state without toggle ambiguity', async () => {
    vi.mocked(postForm).mockResolvedValue({
      ok: true,
      message: 'COP ALERT disarmed',
      cop_alert: {
        active: false,
        ignition_on: false,
        ext_flood: 'off',
        last_ntfy: null,
        last_error: null,
      },
    });

    await setCopAlert(false);

    expect(postForm).toHaveBeenCalledWith('/api/cop-alert', { active: 'false' });
  });

  it('rejects a response that does not echo the explicit requested state', async () => {
    vi.mocked(postForm).mockResolvedValue({
      ok: true,
      message: 'COP ALERT armed',
      cop_alert: {
        active: false,
        ignition_on: false,
        ext_flood: 'off',
        last_ntfy: null,
        last_error: null,
      },
    });

    await expect(setCopAlert(true)).rejects.toThrow(
      'COP ALERT response disagrees with the requested state',
    );
  });

  it('uses the fixed empty Starlink toggle form', async () => {
    vi.mocked(postForm).mockResolvedValue({
      ok: true,
      message: 'Starlink power on',
      starlink: { state: 'on', available: true, changing: false, last_error: null },
    });
    await toggleStarlinkPower();
    expect(postForm).toHaveBeenCalledWith('/api/starlink');
  });
});
