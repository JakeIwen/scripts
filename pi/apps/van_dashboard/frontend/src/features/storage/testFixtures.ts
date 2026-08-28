import { decodeDiskStatusResponse, decodeStoragePolicyResponse } from './decoders';

export function storagePolicyPayload(): Record<string, unknown> {
  return {
    ok: true,
    policy: {
      version: 1,
      disks_enabled: true,
      torrents_enabled: true,
      allow_starlink_torrents: false,
      runtime: {
        disks_mounted: true,
        mounted_disk_labels: ['movingparts'],
        qbittorrent_running: true,
      },
    },
  };
}

export function diskStatusPayload(): Record<string, unknown> {
  return {
    ok: true,
    disk_status: {
      checked_at: 1_700_000_100,
      operation: {
        status: 'idle',
        action: null,
        label: null,
        started_at: null,
        completed_at: null,
        error: null,
      },
      disks: [
        {
          label: 'movingparts',
          role: 'always',
          automatic_mount: true,
          requires_disk_policy: false,
          controllable: true,
          attached: true,
          mounted: true,
          mountpoints: ['/mnt/movingparts'],
          device: '/dev/sda',
          size_bytes: 4_000_000_000_000,
          filesystem: 'ext4',
          expected_mount: '/mnt/movingparts',
          hold_until: null,
          hold_remaining_seconds: 0,
          error: null,
          health: {
            state: 'healthy',
            message: 'Filesystem check passed',
            basis: 'offline_check',
            observation: 'Currently mounted read/write; access checks pass',
            event_scope: 'cleared',
            checked_at: 1_699_900_000,
            read_only: false,
            accessible: true,
            writable: true,
            recent_error_count: 0,
            current_boot_error_count: 0,
            previous_boot_error_count: 0,
            historical_error_count: 1,
            latest_error_at: 1_699_000_000,
            latest_error: 'older transport reset',
            current_error_at: null,
            current_error_message: null,
            repairable: true,
          },
        },
      ],
    },
  };
}

export function sampleStoragePolicy() {
  return decodeStoragePolicyResponse(storagePolicyPayload());
}

export function sampleDiskStatus() {
  return decodeDiskStatusResponse(diskStatusPayload());
}
