import { decodeBackupStatusResponse } from './decoders';

export function backupStatusPayload(running = false): Record<string, unknown> {
  const progress = running
    ? {
        phase: 'borg_create',
        detail: 'Creating Borg archive',
        started_at: 1_700_000_000,
        updated_at: 1_700_000_090,
        elapsed_seconds: 90,
        bytes_processed: 1_073_741_824,
        progress_percent: 42,
      }
    : null;
  return {
    ok: true,
    backups: {
      checked_at: 1_700_000_100,
      health: running ? 'running' : 'attention',
      borg: {
        last_success_at: 1_699_900_000,
        stale_hours: 48,
        stale: false,
        running,
        progress,
      },
      exfat_snapshot: {
        last_success_at: 1_699_000_000,
        stale_hours: 48,
        stale: true,
        running: false,
        progress: null,
      },
      openwrt: {
        last_success_at: 1_699_990_000,
        stale_hours: 72,
        stale: false,
        running: false,
        progress: null,
      },
      hotswaps: [
        {
          label: 'hotspare-a',
          interval_days: 7,
          attached: true,
          device: '/dev/sdc',
          size_bytes: 64_000_000_000,
          mounted: false,
          mountpoints: [],
          last_clone_at: 1_699_500_000,
          due: false,
          stale: false,
        },
      ],
      time_machine: {
        device: 'Jacob MacBook Pro',
        available: true,
        last_backup_at: 1_699_999_000,
        snapshots: [1_699_999_000, 1_699_900_000],
        running: false,
        progress_percent: null,
        bytes_copied: null,
        total_bytes: null,
        updated_at: 1_699_999_100,
        error: null,
      },
      operation: {
        status: running ? 'running' : 'idle',
        kind: running ? 'borg' : null,
        target: null,
        started_at: running ? 1_700_000_000 : null,
        completed_at: null,
        error: null,
      },
      stop: {
        status: 'idle',
        kind: null,
        started_at: null,
        completed_at: null,
        error: null,
      },
    },
  };
}

export function sampleBackupStatus(running = false) {
  return decodeBackupStatusResponse(backupStatusPayload(running));
}
