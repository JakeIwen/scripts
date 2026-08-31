import { connectivityAge, mwanRouteLabel, presentSpeedtest } from './presentation';
import type { ConnectivityStatus, MwanInterface, SpeedtestStatus } from './types';
import './network.css';

export interface OpenWrtTileProps {
  connectivity: ConnectivityStatus | null;
  connectivityError: Error | null;
  connectivityRefreshing: boolean;
  speedtest: SpeedtestStatus | null;
  speedtestError: Error | null;
  speedtestStarting: boolean;
  onStartSpeedtest: () => Promise<void>;
  onOpen: () => void;
}

function internetDotClass(connectivity: ConnectivityStatus | null): string {
  if (connectivity?.internetOnline === true) return 'good';
  if (
    connectivity?.internetOnline === false ||
    (connectivity?.internetOnline === null && connectivity.router.reachable === false)
  ) {
    return 'bad';
  }
  return '';
}

function TileMwanInterfaces({ interfaces }: { interfaces: readonly MwanInterface[] }) {
  return (
    <div className="mwan-list" aria-label="MWAN3 interfaces">
      {interfaces.map((item) => (
        <span
          className={`mwan-chip ${item.state}`}
          key={item.name}
          title={item.detail ?? undefined}
        >
          {item.name} · {item.state}
        </span>
      ))}
    </div>
  );
}

function completedAtTime(completedAt: number | null): string {
  if (completedAt === null) return '';
  return ` @ ${new Date(completedAt * 1_000).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })}`;
}

function tileSpeedDetail(speedtest: SpeedtestStatus | null, fallback: string): string {
  if (!speedtest) return fallback;
  if (speedtest.status === 'complete') {
    const latency = speedtest.latencyMs?.toFixed(1) ?? '—';
    return `Latency ${latency} ms${completedAtTime(speedtest.completedAt)}`;
  }
  if (speedtest.status === 'error') {
    return `${speedtest.error ?? 'Unknown error'}${completedAtTime(speedtest.completedAt)}`;
  }
  return fallback;
}

export function OpenWrtTile({
  connectivity,
  connectivityError,
  connectivityRefreshing,
  speedtest,
  speedtestError,
  speedtestStarting,
  onStartSpeedtest,
  onOpen,
}: OpenWrtTileProps) {
  const route = connectivity
    ? mwanRouteLabel(connectivity.router, connectivity.internetOnline)
    : 'Route unavailable';
  const speed = presentSpeedtest(speedtest);
  const speedtestRunning = speedtest?.status === 'running';
  const age = connectivityError
    ? connectivityError.message
    : connectivityRefreshing && !connectivity
      ? 'Checking…'
      : connectivityAge(connectivity);
  const speedTitle = speedtestError && !speedtest ? 'Speed test unavailable' : speed.title;
  const speedDetail =
    speedtestError && !speedtest
      ? speedtestError.message
      : tileSpeedDetail(speedtest, speed.detail);

  return (
    <article className="tile openwrt-tile" aria-labelledby="openwrt-tile-title">
      <button
        type="button"
        className="openwrt-open"
        aria-label="Open OpenWrt details"
        aria-haspopup="dialog"
        onClick={onOpen}
      />

      <header className="connectivity-head">
        <h2 className="network-card-heading" id="openwrt-tile-title">
          <span className="network-card-icon" aria-hidden="true">
            🌐
          </span>
          <span>OpenWrt</span>
        </h2>
        <span className="connectivity-age">{age}</span>
      </header>

      <div className="mwan-overview">
        <div className="mwan-primary">
          <span className="network-label">
            <span className={`network-dot ${internetDotClass(connectivity)}`} aria-hidden="true" />
            MWAN3 route
          </span>
          <strong className="network-value">{route}</strong>
        </div>
        <TileMwanInterfaces interfaces={connectivity?.router.interfaces ?? []} />
      </div>

      <div className="openwrt-speedtest" aria-label="Speed test">
        <button
          type="button"
          className={`speedtest-button openwrt-speedtest-button ${
            speedtestStarting || speedtestRunning ? 'running' : ''
          }`.trim()}
          disabled={speedtestStarting || speedtestRunning}
          aria-busy={speedtestStarting || speedtestRunning}
          onClick={() => void onStartSpeedtest()}
        >
          <span className="speed-icon" aria-hidden="true">
            ᯓ➤
          </span>
          <span className="speed-spinner" aria-hidden="true" />
          <span>{speedtestStarting || speedtestRunning ? 'Testing…' : 'Speed Test'}</span>
        </button>
        <span
          className={`speed-results ${speed.tone === 'bad' ? 'bad' : ''}`.trim()}
          role="status"
          aria-live="polite"
        >
          <strong>{speedTitle}</strong>
          <span>{speedDetail}</span>
        </span>
      </div>
    </article>
  );
}
