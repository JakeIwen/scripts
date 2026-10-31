import {
  decodeDiskHistoryResponse,
  decodeDiskStatsResponse,
  decodeDiskStatusResponse,
  decodeStoragePolicyResponse,
} from './schema';

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
          space: {
            total_bytes: 4_000_000_000_000,
            used_bytes: 2_500_000_000_000,
            available_bytes: 1_300_000_000_000,
          },
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

function smartReading(at: number, temperature: number) {
  return {
    at,
    passed: true,
    reallocated_sectors: 32,
    pending_sectors: 0,
    uncorrectable_sectors: 0,
    temperature_c: temperature,
    power_on_hours: 12_345,
    power_cycles: 678,
  };
}

export function diskStatsPayload(): Record<string, unknown> {
  return {
    ok: true,
    disk_stats: {
      checked_at: 1_700_000_100,
      io_sample_seconds: 60,
      disks: {
        movingparts: {
          io: {
            at: 1_700_000_080,
            read_bytes_per_second: 3 * 1024 * 1024,
            write_bytes_per_second: 512 * 1024,
            busy_percent: 42.5,
          },
          smart: smartReading(1_700_000_000, 41),
        },
      },
    },
  };
}

/** Two adjacent hourly I/O buckets, then a gap, and two temperature buckets. */
export function diskHistoryPayload(): Record<string, unknown> {
  const bucket = (start: number, minutes: number, read: number) => ({
    start,
    minutes,
    read_bytes: read * minutes * 60,
    write_bytes: 1024 * minutes * 60,
    peak_read_bytes_per_second: read * 2,
    peak_write_bytes_per_second: 2048,
    busy_percent: 10,
    peak_busy_percent: 30,
  });
  return {
    ok: true,
    disk_history: {
      label: 'movingparts',
      since: 1_700_000_000,
      until: 1_700_086_400,
      bucket_seconds: 3600,
      io_sample_seconds: 60,
      io: [
        bucket(1_700_002_800, 60, 1024 * 1024),
        bucket(1_700_006_400, 30, 4 * 1024 * 1024),
        bucket(1_700_020_800, 30, 0),
      ],
      temperature: [
        { start: 1_700_002_800, readings: 6, temperature_c: 40, max_temperature_c: 42 },
        { start: 1_700_006_400, readings: 2, temperature_c: 44, max_temperature_c: 45 },
      ],
      smart: smartReading(1_700_086_000, 43),
    },
  };
}

export function sampleDiskStats() {
  return decodeDiskStatsResponse(diskStatsPayload());
}

export function sampleDiskHistory() {
  return decodeDiskHistoryResponse(diskHistoryPayload());
}

export function sampleStoragePolicy() {
  return decodeStoragePolicyResponse(storagePolicyPayload());
}

export function sampleDiskStatus() {
  return decodeDiskStatusResponse(diskStatusPayload());
}
