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
    // A link that just came up has no baseline in this window.
    if (!before) continue;
    const received = uplink.rxBytes - before.rxBytes;
    const sent = uplink.txBytes - before.txBytes;
    // A recreated device restarts its counters from zero.
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
