import {
  decodeConnectivityResponse,
  decodeOpenWrtClientsResponse,
  decodeSpeedtestResponse,
} from './decoders';

export function connectivityPayload(): Record<string, unknown> {
  return {
    ok: true,
    connectivity: {
      checked_at: 1_700_000_100,
      internet: {
        online: true,
        source: 'mwan3 reachability tracking',
      },
      router: {
        reachable: true,
        mode: 'clientwan',
        online: ['clientwan'],
        interfaces: [
          {
            name: 'clientwan',
            state: 'online',
            tracking: 'active',
            detail: 'tracking active',
          },
          {
            name: 'wan',
            state: 'offline',
            tracking: 'active',
            detail: 'tracking active',
          },
        ],
        default_policy: 'balanced',
        route_members: [{ name: 'clientwan', percent: 100 }],
        error: null,
      },
      ubnt: {
        reachable: true,
        connected: true,
        ssid: 'camp-wifi',
        signal_dbm: -61,
        noise_dbm: -96,
        quality_percent: 74,
        ccq_percent: 88.1,
        bitrate: '65 Mb/s',
        error: null,
      },
      refreshing: false,
      last_error: null,
      active_mode: true,
      stale: false,
    },
  };
}

export function clientsPayload(): Record<string, unknown> {
  return {
    ok: true,
    openwrt: {
      version: 1,
      checked_at: 1_700_000_100,
      client_count: 2,
      wifi_count: 1,
      lan_count: 1,
      clients: [
        {
          name: 'lion_fone',
          hostname_known: true,
          ip: '192.168.6.40',
          mac: '02:11:22:33:44:55',
          connection: 'wifi',
          interface: 'wlan0',
          radio: 'radio0',
          band: '2.4 GHz',
          neighbor_state: 'REACHABLE',
          signal_dbm: -52,
          rx_rate_bps: 86_000_000,
          tx_rate_bps: 72_000_000,
          rx_bytes: 1_048_576,
          tx_bytes: 524_288,
          lease_expires_at: 1_700_003_600,
        },
        {
          name: 'vanpi',
          hostname_known: true,
          ip: '192.168.6.103',
          mac: '02:aa:bb:cc:dd:ee',
          connection: 'lan',
          interface: 'br-lan',
          radio: null,
          band: null,
          neighbor_state: 'PERMANENT',
          signal_dbm: null,
          rx_rate_bps: null,
          tx_rate_bps: null,
          rx_bytes: null,
          tx_bytes: null,
          lease_expires_at: null,
        },
      ],
    },
  };
}

export function speedtestPayload(status: 'idle' | 'running' | 'complete' | 'error' = 'complete') {
  return {
    ok: true,
    speedtest: {
      status,
      started_at: 1_700_000_000,
      completed_at: status === 'running' ? null : 1_700_000_060,
      download_mbps: status === 'complete' ? 42.5 : null,
      upload_mbps: status === 'complete' ? 8.25 : null,
      latency_ms: status === 'complete' ? 31.2 : null,
      error: status === 'error' ? 'upstream timed out' : null,
    },
  };
}

export function sampleConnectivity() {
  return decodeConnectivityResponse(connectivityPayload());
}

export function sampleClients() {
  return decodeOpenWrtClientsResponse(clientsPayload());
}

export function sampleSpeedtest(status: 'idle' | 'running' | 'complete' | 'error' = 'complete') {
  return decodeSpeedtestResponse(speedtestPayload(status));
}
