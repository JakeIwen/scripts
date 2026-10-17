import type { WanTraffic } from './schema';

// Collector samples are 3 s apart while a dashboard is visible and 30 s when idle.
// A longer gap (hidden tab, collector errors) would be history, not current use.
export const MAX_THROUGHPUT_WINDOW_SECONDS = 90;

export interface UplinkThroughput {
  name: string;
  downloadBps: number;
  uploadBps: number;
}

export interface WanThroughput {
  downloadBps: number;
  uploadBps: number;
  windowSeconds: number;
  uplinks: UplinkThroughput[];
}

/** Internet throughput between two router counter samples, summed over all up uplinks. */
export function measureWanThroughput(
  previous: WanTraffic,
  current: WanTraffic,
): WanThroughput | null {
  const windowSeconds = current.uptimeSeconds - previous.uptimeSeconds;
  // A router reboot restarts both its uptime and every counter.
  if (!(windowSeconds > 0) || windowSeconds > MAX_THROUGHPUT_WINDOW_SECONDS) return null;

  const uplinks: UplinkThroughput[] = [];
  for (const uplink of current.uplinks) {
    const before = previous.uplinks.find(
      (item) => item.name === uplink.name && item.device === uplink.device,
    );
    const received = before ? uplink.rxBytes - before.rxBytes : -1;
    const sent = before ? uplink.txBytes - before.txBytes : -1;
    // A link that just came up, or whose device was recreated, has no baseline yet.
    if (received < 0 || sent < 0) continue;
    uplinks.push({
      name: uplink.name,
      downloadBps: (received * 8) / windowSeconds,
      uploadBps: (sent * 8) / windowSeconds,
    });
  }
  if (current.uplinks.length > 0 && uplinks.length === 0) return null;

  return {
    downloadBps: uplinks.reduce((total, item) => total + item.downloadBps, 0),
    uploadBps: uplinks.reduce((total, item) => total + item.uploadBps, 0),
    windowSeconds,
    uplinks,
  };
}

/** Matches the speed test's Mbps so current use reads against measured capacity. */
export function formatBitRate(bitsPerSecond: number): string {
  if (bitsPerSecond < 999_500) return `${Math.round(bitsPerSecond / 1_000)} kbps`;
  const mbps = bitsPerSecond / 1_000_000;
  return `${mbps < 99.95 ? mbps.toFixed(1) : mbps.toFixed(0)} Mbps`;
}

export function throughputSummary(throughput: WanThroughput): string {
  return `↓ ${formatBitRate(throughput.downloadBps)} · ↑ ${formatBitRate(throughput.uploadBps)}`;
}

export function throughputDetail(throughput: WanThroughput): string {
  const uplinks = throughput.uplinks.map(
    (item) =>
      `${item.name} ↓ ${formatBitRate(item.downloadBps)} ↑ ${formatBitRate(item.uploadBps)}`,
  );
  return [...uplinks, `Averaged over ${throughput.windowSeconds.toFixed(1)} s`].join('\n');
}
