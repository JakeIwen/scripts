import type { StatusTone } from '../../components/StatusPill';
import { formatRelativeTime } from '../../utils/format';
import type { ConnectivityStatus, RouterConnectivity, SpeedtestStatus } from './types';

export function mwanRouteLabel(router: RouterConnectivity, internetOnline: boolean | null): string {
  if (router.routeMembers.length > 0) {
    return router.routeMembers
      .map((member) => {
        if (router.routeMembers.length === 1 && member.percent === 100) return member.name;
        return `${member.name} ${member.percent}%`;
      })
      .join(' + ');
  }
  if (internetOnline === false || router.reachable === false) return 'No active route';
  return router.defaultPolicy ? 'No eligible route' : 'Route unavailable';
}

export function connectivityTone(connectivity: ConnectivityStatus | null): StatusTone {
  if (!connectivity) return 'neutral';
  if (connectivity.internetOnline === false || connectivity.router.reachable === false)
    return 'bad';
  if (connectivity.stale || connectivity.lastError) return 'warning';
  return connectivity.internetOnline === true ? 'good' : 'neutral';
}

export function connectivityLabel(connectivity: ConnectivityStatus | null): string {
  if (!connectivity) return 'No data';
  if (connectivity.internetOnline === true) return connectivity.stale ? 'Stale' : 'Online';
  if (connectivity.internetOnline === false || connectivity.router.reachable === false) {
    return 'Offline';
  }
  return connectivity.refreshing ? 'Checking' : 'No data';
}

export function connectivitySummary(connectivity: ConnectivityStatus | null): string {
  if (!connectivity) return 'Waiting for MWAN3 status';
  const route = mwanRouteLabel(connectivity.router, connectivity.internetOnline);
  if (connectivity.internetOnline === true) return `Online via ${route}`;
  if (connectivity.internetOnline === false) return 'Internet offline';
  if (connectivity.router.reachable === false) return 'OpenWrt is unreachable';
  return route;
}

export function connectivityAge(connectivity: ConnectivityStatus | null): string {
  if (!connectivity) return 'Waiting for MWAN3';
  if (connectivity.router.error) return `MWAN3 error · ${connectivity.router.error}`;
  if (connectivity.lastError) return `Collector error · ${connectivity.lastError}`;
  if (connectivity.checkedAt !== null) {
    return `${connectivity.stale ? 'Stale' : 'Updated'} · ${formatRelativeTime(connectivity.checkedAt)}`;
  }
  return connectivity.refreshing ? 'Checking…' : 'Waiting for MWAN3';
}

export interface SpeedtestPresentation {
  tone: StatusTone;
  title: string;
  detail: string;
}

export function presentSpeedtest(speedtest: SpeedtestStatus | null): SpeedtestPresentation {
  if (!speedtest || speedtest.status === 'idle') {
    return { tone: 'neutral', title: 'Not run yet', detail: "Uses vanpi's current route" };
  }
  if (speedtest.status === 'running') {
    return { tone: 'warning', title: 'Testing current route…', detail: 'This can take a minute' };
  }
  if (speedtest.status === 'error') {
    return {
      tone: 'bad',
      title: 'Speed test failed',
      detail: speedtest.error ?? 'Unknown error',
    };
  }
  const download = speedtest.downloadMbps?.toFixed(1) ?? '—';
  const upload = speedtest.uploadMbps?.toFixed(1) ?? '—';
  const latency = speedtest.latencyMs?.toFixed(1) ?? '—';
  return {
    tone: 'good',
    title: `↓ ${download} Mbps · ↑ ${upload} Mbps`,
    detail: `Latency ${latency} ms${
      speedtest.completedAt === null ? '' : ` · ${formatRelativeTime(speedtest.completedAt)}`
    }`,
  };
}
