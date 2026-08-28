import { StatusPill, type StatusTone } from '../../components/StatusPill';
import { Tile } from '../../components/Tile';
import { MwanInterfaceList } from './MwanInterfaceList';
import {
  connectivityLabel,
  connectivitySummary,
  connectivityTone,
  mwanRouteLabel,
  presentSpeedtest,
} from './presentation';
import type { ConnectivityStatus, SpeedtestStatus } from './types';
import './network.css';

export interface OpenWrtTileProps {
  connectivity: ConnectivityStatus | null;
  connectivityError: Error | null;
  connectivityRefreshing: boolean;
  speedtest: SpeedtestStatus | null;
  speedtestError: Error | null;
  onOpen: () => void;
}

function resourceTone(data: ConnectivityStatus | null, error: Error | null): StatusTone {
  if (error) return data ? 'warning' : 'bad';
  return connectivityTone(data);
}

export function OpenWrtTile({
  connectivity,
  connectivityError,
  connectivityRefreshing,
  speedtest,
  speedtestError,
  onOpen,
}: OpenWrtTileProps) {
  const route = connectivity
    ? mwanRouteLabel(connectivity.router, connectivity.internetOnline)
    : 'Route unavailable';
  const speed = presentSpeedtest(speedtest);
  const tone = resourceTone(connectivity, connectivityError);
  const statusLabel =
    connectivityError && !connectivity
      ? 'Unavailable'
      : connectivityRefreshing && !connectivity
        ? 'Checking'
        : connectivityLabel(connectivity);

  return (
    <Tile
      icon="🌐"
      title="OpenWrt"
      summary={
        connectivityError && !connectivity
          ? connectivityError.message
          : connectivitySummary(connectivity)
      }
      status={<StatusPill tone={tone}>{statusLabel}</StatusPill>}
      tone={tone}
      onClick={onOpen}
      ariaLabel="Open OpenWrt details"
      className="openwrt-tile"
    >
      <div className="openwrt-tile__route">
        <span>MWAN3 route</span>
        <strong>{route}</strong>
      </div>
      <MwanInterfaceList interfaces={connectivity?.router.interfaces ?? []} />
      <div className={`openwrt-speed openwrt-speed--${speed.tone}`} aria-label="Speed test status">
        <span className="openwrt-speed__icon" aria-hidden="true">
          ᯓ➤
        </span>
        <span>
          <strong>{speedtestError && !speedtest ? 'Speed test unavailable' : speed.title}</strong>
          <small>{speedtestError && !speedtest ? speedtestError.message : speed.detail}</small>
        </span>
      </div>
    </Tile>
  );
}
