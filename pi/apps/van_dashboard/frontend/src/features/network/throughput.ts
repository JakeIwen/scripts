import type { WanTraffic } from './schema';

// Collector samples are 3 s apart while a dashboard is visible and 30 s when idle.
// A longer gap (hidden tab, collector errors) would be history, not current use.
export const MAX_THROUGHPUT_WINDOW_SECONDS = 90;

export interface UplinkThroughput {
  name: string;
  downloadBytesPerSecond: number;
  uploadBytesPerSecond: number;
}

export interface WanThroughput {
  downloadBytesPerSecond: number;
  uploadBytesPerSecond: number;
  windowSeconds: number;
  uplinks: UplinkThroughput[];
  /** Traffic switched between van devices; null without LAN counters in both samples. */
  localBytesPerSecond: number | null;
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
      downloadBytesPerSecond: received / windowSeconds,
      uploadBytesPerSecond: sent / windowSeconds,
    });
  }
  if (current.uplinks.length > 0 && uplinks.length === 0) return null;

  return {
    downloadBytesPerSecond: uplinks.reduce((total, item) => total + item.downloadBytesPerSecond, 0),
    uploadBytesPerSecond: uplinks.reduce((total, item) => total + item.uploadBytesPerSecond, 0),
    windowSeconds,
    uplinks,
    localBytesPerSecond: measureLocalTraffic(previous, current, windowSeconds),
  };
}

function measureLocalTraffic(
  previous: WanTraffic,
  current: WanTraffic,
  windowSeconds: number,
): number | null {
  if (!previous.lan || !current.lan) return null;
  const received = current.lan.portsRxBytes - previous.lan.portsRxBytes;
  const passedUp = current.lan.bridgeRxBytes - previous.lan.bridgeRxBytes;
  // A restarted access point or port restarts its counters from zero.
  if (received < 0 || passedUp < 0) return null;
  // Broadcasts are both switched and passed up, so a quiet LAN can dip below zero.
  return Math.max(0, received - passedUp) / windowSeconds;
}
