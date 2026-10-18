import { describe, expect, it, vi } from 'vitest';

import { postForm } from '../../api/client';
import { setCopAlert } from './api';

vi.mock('../../api/client', () => ({ postForm: vi.fn() }));

describe('status mutations', () => {
  it('sends an explicit desired COP ALERT state in both directions without toggle ambiguity', async () => {
    vi.mocked(postForm).mockResolvedValueOnce({
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
    expect(postForm).toHaveBeenLastCalledWith('/api/cop-alert', { active: 'true' });

    vi.mocked(postForm).mockResolvedValueOnce({
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
    expect(postForm).toHaveBeenLastCalledWith('/api/cop-alert', { active: 'false' });
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
});
