import { renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { useWanThroughput } from './hooks';
import { formatByteRate, presentWanThroughput } from './presentation';
import { decodeConnectivityResponse } from './schema';
import { connectivityPayload, sampleConnectivity } from './testFixtures';
import { MAX_THROUGHPUT_WINDOW_SECONDS, measureWanThroughput } from './throughput';
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
    lan: null,
  } satisfies WanTraffic;
}

function withLan(sample: WanTraffic, portsRxBytes: number, bridgeRxBytes: number): WanTraffic {
  return { ...sample, lan: { portsRxBytes, bridgeRxBytes } };
}

describe('WAN throughput', () => {
  it('sums every up uplink over the router clock window', () => {
    const result = measureWanThroughput(
      traffic(100, ['wan', 'wan', 1_000, 2_000], ['clientwan', 'wl1-sta0', 0, 0]),
      traffic(102, ['wan', 'wan', 501_000, 27_000], ['clientwan', 'wl1-sta0', 250_000, 0]),
    );
    expect(result).toEqual({
      downloadBytesPerSecond: 375_000,
      uploadBytesPerSecond: 12_500,
      windowSeconds: 2,
      uplinks: [
        { name: 'wan', downloadBytesPerSecond: 250_000, uploadBytesPerSecond: 12_500 },
        { name: 'clientwan', downloadBytesPerSecond: 125_000, uploadBytesPerSecond: 0 },
      ],
      localBytesPerSecond: null,
    });
  });

  it('measures local traffic as bridge-port receives the router did not take up', () => {
    const before = withLan(traffic(100, ['wan', 'wan', 0, 0]), 10_000, 4_000);
    const after = withLan(traffic(102, ['wan', 'wan', 0, 0]), 1_010_000, 404_000);
    expect(measureWanThroughput(before, after)?.localBytesPerSecond).toBe(300_000);
    // A restarted access point resets its counter: no local reading for that window.
    const reset = withLan(traffic(102, ['wan', 'wan', 0, 0]), 5_000, 404_000);
    expect(measureWanThroughput(before, reset)?.localBytesPerSecond).toBeNull();
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
    expect(result?.downloadBytesPerSecond).toBe(125);
  });

  it('reports zero when no uplink is up', () => {
    expect(measureWanThroughput(traffic(100), traffic(103))).toMatchObject({
      downloadBytesPerSecond: 0,
      uploadBytesPerSecond: 0,
      uplinks: [],
    });
  });

  it('presents a hidden, unavailable, stale, measuring or measured row', () => {
    const connectivity = sampleConnectivity();
    const measured = measureWanThroughput(
      traffic(100, ['wan', 'wan', 0, 0]),
      traffic(102, ['wan', 'wan', 750_000, 25_000]),
    );
    expect(presentWanThroughput(null, measured)).toBeNull();
    expect(presentWanThroughput(connectivity, measured)).toBeNull();

    connectivity.router.wanTraffic = null;
    expect(presentWanThroughput(connectivity, measured)).toEqual({
      summary: '—',
      detail: null,
      local: null,
    });
    connectivity.router.wanTraffic = traffic(102);
    expect(presentWanThroughput({ ...connectivity, stale: true }, measured)?.summary).toBe('—');
    expect(presentWanThroughput(connectivity, null)?.summary).toBe('Measuring…');
    expect(presentWanThroughput(connectivity, measured)).toEqual({
      summary: '↓ 375 kB/s · ↑ 13 kB/s',
      detail: 'wan ↓ 375 kB/s · ↑ 13 kB/s\nAveraged over 2.0 s',
      local: null,
    });
    connectivity.router.wanTraffic = withLan(traffic(102), 0, 0);
    expect(
      presentWanThroughput(connectivity, { ...measured!, localBytesPerSecond: 2_500_000 })?.local,
    ).toBe('2.5 MB/s');
  });

  it('formats byte rates in kB/s below one megabyte and MB/s above', () => {
    expect(formatByteRate(999_499)).toBe('999 kB/s');
    expect(formatByteRate(1_540_000)).toBe('1.5 MB/s');
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
    expect(result.current?.downloadBytesPerSecond).toBe(125_000);
    rerender({ sample: traffic(103, ['wan', 'wan', 375_000, 0]) });
    expect(result.current?.windowSeconds).toBe(3);

    rerender({ sample: null });
    rerender({ sample: traffic(106, ['wan', 'wan', 375_000, 750]) });
    expect(result.current).toMatchObject({ downloadBytesPerSecond: 0, uploadBytesPerSecond: 250 });
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

    router.wan_traffic = {
      uptime_seconds: 1,
      uplinks: [],
      lan: { ports_rx_bytes: 9, bridge_rx_bytes: 4 },
    };
    expect(decodeConnectivityResponse(payload).router.wanTraffic?.lan).toEqual({
      portsRxBytes: 9,
      bridgeRxBytes: 4,
    });

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
