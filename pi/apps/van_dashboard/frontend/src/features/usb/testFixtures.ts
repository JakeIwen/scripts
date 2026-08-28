import { decodeUsbStatusResponse } from './decoders';

export function usbStatusPayload(): Record<string, unknown> {
  const diskInstance = {
    device_number: 4,
    location: '2-2.1',
    parent_location: '2-2',
    port: 1,
    labels: ['movingparts'],
  };
  return {
    ok: true,
    usb: {
      checked_at: 1_700_000_100,
      last_success_at: 1_700_000_100,
      last_error: null,
      present_device_count: 1,
      unplugged_device_count: 0,
      storage_labels: ['movingparts'],
      devices: [
        {
          bus: '002',
          device_id: '1d6b:0003',
          description: 'Linux root hub',
          present_count: 1,
          labels: [],
          root_hub: true,
          instances: [],
          max_count: 1,
          known_instances: [],
          event: null,
          status: 'root',
        },
        {
          bus: '002',
          device_id: '0bc2:ab24',
          description: 'Seagate movingparts',
          present_count: 1,
          labels: ['movingparts'],
          root_hub: false,
          instances: [diskInstance],
          max_count: 1,
          known_instances: [diskInstance],
          event: { kind: 'replugged', at: 1_700_000_000 },
          status: 'present',
        },
      ],
    },
    usb_ports: {
      loaded: true,
      checked_at: 1_700_000_090,
      expires_at: 1_700_000_390,
      last_error: null,
      hubs: [
        {
          location: '2-2',
          description: 'External USB hub',
          detail: 'Genesys hub · 4 physical ports',
          device_id: '05e3:0626',
          method: 'power',
          physical: true,
          advanced: false,
          ports: [
            {
              key: '2-2:1',
              location: '2-2',
              port: 1,
              method: 'power',
              enabled: true,
              device_descriptions: ['Seagate movingparts'],
              downstream_device_count: 1,
              storage_labels: ['movingparts'],
              mounted_labels: [],
              topology_locations: ['1-1.2:1', '2-2:1'],
            },
          ],
        },
      ],
      operation: { status: 'idle' },
      expired: false,
    },
  };
}

export function sampleUsbStatus() {
  return decodeUsbStatusResponse(usbStatusPayload());
}
