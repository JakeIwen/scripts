import { renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { useWanThroughput } from './hooks';
import { decodeConnectivityResponse } from './schema';
import { connectivityPayload } from './testFixtures';
import {
  formatBitRate,
  MAX_THROUGHPUT_WINDOW_SECONDS,
  measureWanThroughput,
  throughputSummary,
} from './throughput';
import type { WanTraffic } from './types';

function traffic(uptimeSeconds: number, ...uplinks: [string, string, number, number][]) {
  return {
    uptimeSeconds,
    uplinks: uplinks.map(([name, device, rxBytes, txBytes]) => ({
      name,
      device,
      rxBytes,
      txBytes,
    })),
  } satisfies WanTraffic;
}

describe('WAN throughput', () => {
  it('sums every up uplink over the router clock window', () => {
    const result = measureWanThroughput(
      traffic(100, ['wan', 'wan', 1_000, 2_000], ['clientwan', 'wl1-sta0', 0, 0]),
      traffic(102, ['wan', 'wan', 501_000, 27_000], ['clientwan', 'wl1-sta0', 250_000, 0]),
    );
    expect(result).toEqual({
      downloadBps: 3_000_000,
      uploadBps: 100_000,
      windowSeconds: 2,
      uplinks: [
        { name: 'wan', downloadBps: 2_000_000, uploadBps: 100_000 },
        { name: 'clientwan', downloadBps: 1_000_000, uploadBps: 0 },
      ],
    });
    expect(result && throughputSummary(result)).toBe('↓ 3.0 Mbps · ↑ 100 kbps');
  });

  it('has no reading across a reboot, a long gap, or only unbaselined links', () => {
    const before = traffic(100, ['wan', 'wan', 5_000, 5_000]);
    for (const after of [
      traffic(50, ['wan', 'wan', 9_000, 9_000]),
      traffic(100, ['wan', 'wan', 9_000, 9_000]),
      traffic(101 + MAX_THROUGHPUT_WINDOW_SECONDS, ['wan', 'wan', 9_000, 9_000]),
      traffic(103, ['clientwan', 'wl1-sta0', 9_000, 9_000]),
      traffic(103, ['wan', 'wan', 10, 9_000]),
    ]) {
      expect(measureWanThroughput(before, after)).toBeNull();
    }
  });

  it('skips a recreated device but keeps the links it can measure', () => {
    const result = measureWanThroughput(
      traffic(100, ['wan', 'wan', 0, 0], ['clientwan', 'wl1-sta0', 9_000, 9_000]),
      traffic(101, ['wan', 'wan', 125, 0], ['clientwan', 'wl1-sta0', 10, 10]),
    );
    expect(result?.uplinks.map((item) => item.name)).toEqual(['wan']);
    expect(result?.downloadBps).toBe(1_000);
  });

  it('reports zero when no uplink is up', () => {
    expect(measureWanThroughput(traffic(100), traffic(103))).toMatchObject({
      downloadBps: 0,
      uploadBps: 0,
      uplinks: [],
    });
  });

  it('formats bit rates like the speed test', () => {
    expect(formatBitRate(0)).toBe('0 kbps');
    expect(formatBitRate(999_499)).toBe('999 kbps');
    expect(formatBitRate(999_500)).toBe('1.0 Mbps');
    expect(formatBitRate(42_460_000)).toBe('42.5 Mbps');
    expect(formatBitRate(250_000_000)).toBe('250 Mbps');
  });

  it('measures only when the collector produces a new router sample', () => {
    const { result, rerender } = renderHook(
      ({ sample }: { sample: WanTraffic | null | undefined }) => useWanThroughput(sample),
      { initialProps: { sample: traffic(100, ['wan', 'wan', 0, 0]) as WanTraffic | null } },
    );
    expect(result.current).toBeNull();

    // Re-polling the same collector sample must not shrink the window to zero.
    rerender({ sample: traffic(100, ['wan', 'wan', 0, 0]) });
    expect(result.current).toBeNull();

    rerender({ sample: traffic(103, ['wan', 'wan', 375_000, 0]) });
    expect(result.current?.downloadBps).toBe(1_000_000);
    rerender({ sample: traffic(103, ['wan', 'wan', 375_000, 0]) });
    expect(result.current?.windowSeconds).toBe(3);

    rerender({ sample: null });
    rerender({ sample: traffic(106, ['wan', 'wan', 375_000, 750]) });
    expect(result.current).toMatchObject({ downloadBps: 0, uploadBps: 2_000 });
  });

  it('decodes router counters and rejects negative byte counts', () => {
    const payload = connectivityPayload();
    const router = (payload.connectivity as Record<string, Record<string, unknown>>).router;
    if (!router) throw new Error('fixture has no router');
    router.wan_traffic = {
      uptime_seconds: 3_833_465.59,
      uplinks: [{ name: 'wan', device: 'wan', rx_bytes: 793_589_244_303, tx_bytes: 6 }],
    };
    expect(decodeConnectivityResponse(payload).router.wanTraffic).toEqual(
      traffic(3_833_465.59, ['wan', 'wan', 793_589_244_303, 6]),
    );

    router.wan_traffic = null;
    expect(decodeConnectivityResponse(payload).router.wanTraffic).toBeNull();

    router.wan_traffic = {
      uptime_seconds: 1,
      uplinks: [{ name: 'wan', device: 'wan', rx_bytes: -1, tx_bytes: 0 }],
    };
    expect(() => decodeConnectivityResponse(payload)).toThrow(TypeError);

    delete router.wan_traffic;
    expect('wanTraffic' in decodeConnectivityResponse(payload).router).toBe(false);
  });
});
