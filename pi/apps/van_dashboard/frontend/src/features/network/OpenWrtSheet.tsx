import { BottomSheet } from '../../components/BottomSheet';
import { formatBytes, formatRelativeTime } from '../../utils/format';
import { MwanInterfaceList } from './MwanInterfaceList';
import {
  connectivityAge,
  connectivityTone,
  mwanRouteLabel,
  presentSpeedtest,
} from './presentation';
import type {
  ConnectivityStatus,
  OpenWrtClient,
  OpenWrtClientStatus,
  SpeedtestStatus,
} from './types';
import './network.css';

export interface OpenWrtSheetProps {
  open: boolean;
  onClose: () => void;
  connectivity: ConnectivityStatus | null;
  connectivityError: Error | null;
  clients: OpenWrtClientStatus | null;
  clientsError: Error | null;
  clientsInitialLoading: boolean;
  clientsRefreshing: boolean;
  onRefreshClients: () => Promise<OpenWrtClientStatus | null>;
  speedtest: SpeedtestStatus | null;
  speedtestError: Error | null;
  speedtestStarting: boolean;
  onStartSpeedtest: () => Promise<void>;
}

function clientConnectionLabel(client: OpenWrtClient): string {
  if (client.connection === 'lan') return 'LAN';
  return [client.band, client.radio].filter(Boolean).join(' · ') || 'Wi-Fi';
}

function clientDetail(client: OpenWrtClient): string {
  return [
    client.ip ?? 'IPv4 unavailable',
    client.signalDbm === null ? null : `${client.signalDbm} dBm`,
    client.neighborState?.toLowerCase() ?? null,
  ]
    .filter((value): value is string => Boolean(value))
    .join(' · ');
}

function clientTraffic(client: OpenWrtClient): string | null {
  if (client.receivedBytes === null || client.transmittedBytes === null) return null;
  return `↓ ${formatBytes(client.receivedBytes)} · ↑ ${formatBytes(client.transmittedBytes)}`;
}

function ClientRow({ client }: { client: OpenWrtClient }) {
  const traffic = clientTraffic(client);
  return (
    <article className="openwrt-client">
      <span className="openwrt-status-dot openwrt-status-dot--good" aria-hidden="true" />
      <div className="openwrt-client__identity">
        <strong>{client.name}</strong>
        <small>{clientDetail(client)}</small>
      </div>
      <span className="openwrt-client__connection">{clientConnectionLabel(client)}</span>
      <div className="openwrt-client__address">
        <code>{client.mac}</code>
        {traffic && <small>{traffic}</small>}
      </div>
    </article>
  );
}

export function OpenWrtSheet({
  open,
  onClose,
  connectivity,
  connectivityError,
  clients,
  clientsError,
  clientsInitialLoading,
  clientsRefreshing,
  onRefreshClients,
  speedtest,
  speedtestError,
  speedtestStarting,
  onStartSpeedtest,
}: OpenWrtSheetProps) {
  const route = connectivity
    ? mwanRouteLabel(connectivity.router, connectivity.internetOnline)
    : 'Route unavailable';
  const tone = connectivityTone(connectivity);
  const speed = presentSpeedtest(speedtest);
  const speedtestRunning = speedtest?.status === 'running';
  const clientSummary = clients
    ? `${clients.clientCount} connected · ${clients.wifiCount} Wi-Fi · ` +
      `${clients.lanCount} LAN · ${formatRelativeTime(clients.checkedAt)}`
    : clientsInitialLoading
      ? 'Checking…'
      : 'No client data';

  return (
    <BottomSheet
      open={open}
      title="OpenWrt"
      description={
        'Current uplink policy and devices actively associated with or reachable ' +
        'through the van router.'
      }
      onClose={onClose}
    >
      <div className="openwrt-sheet__status" role="status">
        {connectivityAge(connectivity)}
      </div>
      {connectivityError && <p className="error-message">{connectivityError.message}</p>}

      <div className="openwrt-overview">
        <section className="openwrt-overview__card">
          <span className={`openwrt-status-dot openwrt-status-dot--${tone}`} aria-hidden="true" />
          <span>
            <strong>Internet</strong>
            <small>
              {connectivity?.internetOnline === true
                ? `Online via ${route}`
                : connectivity?.internetOnline === false
                  ? 'Offline'
                  : 'No data'}
            </small>
          </span>
        </section>
        <section className="openwrt-overview__card openwrt-overview__card--wide">
          <span
            className={`openwrt-status-dot openwrt-status-dot--${
              connectivity?.router.reachable === true
                ? 'good'
                : connectivity?.router.reachable === false
                  ? 'bad'
                  : 'neutral'
            }`}
            aria-hidden="true"
          />
          <span>
            <strong>MWAN3</strong>
            <small>
              Route · {route}
              {connectivity?.router.defaultPolicy
                ? ` · ${connectivity.router.defaultPolicy} policy`
                : ''}
            </small>
          </span>
          <MwanInterfaceList interfaces={connectivity?.router.interfaces ?? []} />
        </section>
      </div>

      <section className={`openwrt-speedtest-control openwrt-speed--${speed.tone}`}>
        <div>
          <span className="openwrt-speed__icon" aria-hidden="true">
            ᯯ➔
          </span>
          <span>
            <strong>{speedtestError && !speedtest ? 'Speed test unavailable' : speed.title}</strong>
            <small>{speedtestError && !speedtest ? speedtestError.message : speed.detail}</small>
          </span>
        </div>
        <button
          type="button"
          className="secondary-button"
          disabled={speedtestStarting || speedtestRunning || !open}
          aria-busy={speedtestStarting || speedtestRunning}
          onClick={() => void onStartSpeedtest()}
        >
          {speedtestStarting || speedtestRunning ? 'Testing…' : 'Run speed test'}
        </button>
      </section>

      <section className="openwrt-clients" aria-labelledby="openwrt-clients-title">
        <header className="openwrt-clients__header">
          <div>
            <h3 id="openwrt-clients-title">Connected devices</h3>
            <p>
              Wi-Fi associations include radio details; active LAN neighbors use DHCP and static
              reservations for names and addresses.
            </p>
          </div>
          <div className="openwrt-clients__actions">
            <span aria-live="polite">{clientSummary}</span>
            <button
              className="secondary-button"
              type="button"
              disabled={clientsRefreshing || !open}
              onClick={() => void onRefreshClients()}
            >
              {clientsRefreshing ? 'Refreshing…' : 'Refresh'}
            </button>
          </div>
        </header>

        {clientsError && <p className="error-message">{clientsError.message}</p>}
        <div className="openwrt-client-list" aria-busy={clientsRefreshing || clientsInitialLoading}>
          {clientsInitialLoading && !clients ? (
            <p className="openwrt-client-list__empty">Querying OpenWrt…</p>
          ) : clients?.clients.length ? (
            clients.clients.map((client) => <ClientRow client={client} key={client.mac} />)
          ) : (
            <p className="openwrt-client-list__empty">
              No associated Wi-Fi clients or active LAN neighbors were reported.
            </p>
          )}
        </div>
      </section>
    </BottomSheet>
  );
}
