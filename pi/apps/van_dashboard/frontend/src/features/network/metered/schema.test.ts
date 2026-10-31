import { describe, expect, it } from 'vitest';
import { decodeMeteredStatus } from './schema';
import { meteredFixture } from './testFixtures';

describe('metered decoder', () => {
  it('decodes the full response and the initial null Mac report', () => {
    const payload = meteredFixture();
    expect(decodeMeteredStatus(JSON.parse(JSON.stringify(payload)))).toEqual(payload);
    payload.mac_report = { ...payload.state, host: 'mac' };
    expect(decodeMeteredStatus(payload).mac_report?.host).toBe('mac');
  });

  it.each([
    [
      'ok',
      (row: Record<string, unknown>) => {
        row.ok = false;
      },
    ],
    [
      'state',
      (row: Record<string, unknown>) => {
        row.state = null;
      },
    ],
    [
      'mac_report',
      (row: Record<string, unknown>) => {
        row.mac_report = {};
      },
    ],
    [
      'registry',
      (row: Record<string, unknown>) => {
        row.registry = [{ how: 'paused' }];
      },
    ],
    [
      'policy',
      (row: Record<string, unknown>) => {
        row.policy = { revision: '3' };
      },
    ],
  ])('rejects malformed %s', (_name, corrupt) => {
    const payload = JSON.parse(JSON.stringify(meteredFixture()));
    corrupt(payload);
    expect(() => decodeMeteredStatus(payload)).toThrow(TypeError);
  });

  it('rejects unknown actual states without discarding a valid unknown verdict', () => {
    const payload = meteredFixture();
    payload.state.verdict.metered = null;
    expect(decodeMeteredStatus(payload).state.verdict.metered).toBeNull();
    expect(() =>
      decodeMeteredStatus({
        ...payload,
        state: {
          ...payload.state,
          consumers: {
            'pi.torrents': { ...payload.state.consumers['pi.torrents'], actual: 'paused' },
          },
        },
      }),
    ).toThrow(TypeError);
  });
});
