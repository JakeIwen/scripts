import { decodeUbntWifiStatus } from './decoder';
import type { UbntWifiStatus } from './types';

export function ubntPayload(
  operationStatus: 'idle' | 'running' | 'complete' | 'error' = 'complete',
) {
  return {
    ok: true,
    wifi: {
      version: 1,
      reachable: true,
      checked_at: 1_775_000_000,
      state: {
        configured_ssid: 'denlink',
        associated_ssid: 'denlink',
        ccq_percent: 99.1,
        automatic_paused: false,
        selector_running: true,
        signal_dbm: -56,
        noise_dbm: -93,
        snr_db: 37,
      },
      profiles: [
        {
          name: 'denlink',
          ssid: 'denlink',
          security: 'wpa',
          priority: 10,
          bssid: '4E:EA:85:26:34:F4',
          has_password: true,
          output_power_dbm: 21,
          rate_module: 'atheros',
          rate_auto: true,
          rate_mcs: 4,
        },
        {
          name: 'Camp Guest',
          ssid: 'Guest WiFi',
          security: 'none',
          priority: 20,
          bssid: '',
          has_password: false,
          output_power_dbm: 18,
          rate_module: 'ewma_ht',
          rate_auto: false,
          rate_mcs: 6,
        },
      ],
      networks: [
        {
          ssid: 'denlink',
          security: 'wpa',
          quality_percent: 90,
          frequency_mhz: 2_462,
          channel: 11,
          bssid: '4E:EA:85:26:34:F4',
          signal_dbm: -56,
          profiles: ['denlink'],
          known: true,
          connected: true,
          supported: true,
        },
        {
          ssid: 'Campus',
          security: 'enterprise',
          quality_percent: 40,
          frequency_mhz: 2_422,
          channel: 3,
          bssid: '00:11:22:33:44:77',
          signal_dbm: -65,
          profiles: [],
          known: false,
          connected: false,
          supported: false,
        },
        {
          ssid: 'Camp Secure',
          security: 'wpa',
          quality_percent: 62,
          frequency_mhz: 2_437,
          channel: 6,
          bssid: '00:11:22:33:44:88',
          signal_dbm: -63,
          profiles: [],
          known: false,
          connected: false,
          supported: true,
        },
        {
          ssid: 'Park Guest',
          security: 'none',
          quality_percent: 52,
          frequency_mhz: 2_412,
          channel: 1,
          bssid: '00:11:22:33:44:99',
          signal_dbm: -68,
          profiles: [],
          known: false,
          connected: false,
          supported: true,
        },
      ],
    },
    operation: {
      status: operationStatus,
      kind: operationStatus === 'idle' ? null : 'status',
      started_at: operationStatus === 'idle' ? null : 1_774_999_998,
      completed_at:
        operationStatus === 'running' || operationStatus === 'idle' ? null : 1_775_000_000,
      message: operationStatus === 'complete' ? 'UBNT status refreshed' : null,
      error: operationStatus === 'error' ? 'UBNT status timed out' : null,
    },
  };
}

export function sampleUbntStatus(
  operationStatus: 'idle' | 'running' | 'complete' | 'error' = 'complete',
): UbntWifiStatus {
  return decodeUbntWifiStatus(ubntPayload(operationStatus));
}
