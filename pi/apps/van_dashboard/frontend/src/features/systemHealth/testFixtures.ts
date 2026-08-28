import { decodeSystemHealthReport } from './decoder';
import type { SystemHealthRange, SystemHealthReport } from './types';

export function systemHealthPayload(range: SystemHealthRange = 6) {
  return {
    ok: true,
    version: 4,
    generated_at: 1_775_000_000,
    range: { hours: range, since: 1_774_978_400, until: 1_775_000_000 },
    status: {
      available: true,
      stale: false,
      sample_age_seconds: 3.4,
      current: {
        timestamp: 1_774_999_997,
        cpu_percent: 18.4,
        memory: { used_percent: 42.7, available_bytes: 1_500_000_000 },
        thermal_sensors: [
          { type: 'cpu-thermal', temperature_c: 57.3, producer_detail: { ignored: true } },
        ],
        arm_mhz: 1_500,
        throttle: { hex: '0x0', current: [], occurred: [] },
        network_io: { rx_bytes_per_second: 2_048, tx_bytes_per_second: 1_024 },
        disk_io: {
          read_bytes_per_second: 4_096,
          write_bytes_per_second: 8_192,
          busy_percent: 7.5,
        },
      },
    },
    summary: { events: 2, shown_events: 2, by_severity: {}, by_category: {}, by_kind: {} },
    diagnosis: {
      level: 'warning',
      headline: 'USB faults are present without confirmed undervoltage',
      findings: ['The kernel recorded one USB reset.'],
      next_steps: ['Inspect the hub and cable.'],
      evidence: {
        undervoltage_episodes: 0,
        undervoltage_seconds: 0,
        usb_failures: 1,
        storage_errors: 0,
        episodes: [{ producer_owned: true }],
      },
    },
    peaks: {
      cpu_percent: { value: 73.2, at: 1_774_998_000, top_process: { ignored: true } },
      memory_percent: { value: 61.8, at: 1_774_997_000 },
      temperature_c: { value: 64.1, at: 1_774_996_000 },
      network_rx_bytes_per_second: { value: 50_000, at: 1_774_995_000 },
      network_tx_bytes_per_second: { value: 20_000, at: 1_774_995_000 },
      disk_read_bytes_per_second: { value: 90_000, at: 1_774_994_000 },
      disk_write_bytes_per_second: { value: 40_000, at: 1_774_994_000 },
      disk_busy_percent: { value: 22.4, at: 1_774_994_000 },
    },
    processes: {
      rollup_count: 20,
      current_cpu: [{ producer_owned: true }],
      repeat_offenders: [
        {
          name: 'smbd',
          peak_count: 4,
          cpu_peak_count: 3,
          memory_peak_count: 1,
          max_cpu_percent: 81.2,
          max_rss_bytes: 250_000_000,
          last_seen_at: 1_774_998_000,
          command_line: 'must not be projected',
        },
      ],
    },
    events: [
      {
        timestamp: 1_774_999_000,
        severity: 'warning',
        category: 'usb',
        summary: 'USB device reset',
        message: 'The kernel reset a downstream USB device.',
        state: { producer_owned: true },
      },
      {
        timestamp: 1_774_998_000,
        severity: 'info',
        category: 'system',
        summary: 'Monitor started',
        message: 'Sampling resumed.',
      },
    ],
  };
}

export function sampleSystemHealth(range: SystemHealthRange = 6): SystemHealthReport {
  return decodeSystemHealthReport(systemHealthPayload(range), range);
}
