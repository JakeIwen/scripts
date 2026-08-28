import { describe, expect, it } from 'vitest';

import { decodeSystemHealthReport } from './decoder';
import { projectCurrentSystemSample } from './projections';
import { systemHealthPayload } from './testFixtures';

describe('system health projections', () => {
  it('projects the stable outer report and selected nested metrics', () => {
    const report = decodeSystemHealthReport(systemHealthPayload(24), 24);

    expect(report.rangeHours).toBe(24);
    expect(report.level).toBe('warning');
    expect(report.current?.temperatureCelsius).toBe(57.3);
    expect(report.peaks.cpuPercent.value).toBe(73.2);
    expect(report.evidence.usbFailures).toBe(1);
    expect(report.repeatOffenders[0]).toEqual({
      name: 'smbd',
      peakCount: 4,
      cpuPeakCount: 3,
      memoryPeakCount: 1,
      maximumCpuPercent: 81.2,
      maximumResidentBytes: 250_000_000,
      lastSeenAt: 1_774_998_000,
    });
    expect(report.events[0]?.summary).toBe('USB device reset');
  });

  it('isolates malformed producer-owned details instead of leaking unknown fields', () => {
    const sample = projectCurrentSystemSample({
      timestamp: 10,
      cpu_percent: 7,
      thermal_sensors: [{ type: 'cpu-thermal', temperature_c: 'hot' }],
      memory: 'new producer shape',
      another_future_field: { deeply: ['nested'] },
    });

    expect(sample?.cpuPercent).toBe(7);
    expect(sample?.temperatureCelsius).toBeNull();
    expect(sample?.memoryPercent).toBeNull();
    expect(sample).not.toHaveProperty('another_future_field');
  });

  it('rejects an unknown diagnosis level in the stable outer contract', () => {
    const payload = systemHealthPayload();
    payload.diagnosis.level = 'excellent';
    expect(() => decodeSystemHealthReport(payload, 6)).toThrow(
      'diagnosis.level has an unsupported value: excellent',
    );
  });

  it('supports the bounded route contract when optional producer sections are absent', () => {
    const report = decodeSystemHealthReport(
      {
        ok: true,
        status: { available: true, stale: false, current: {} },
        diagnosis: { level: 'good', headline: 'No faults' },
        peaks: {},
        events: [],
      },
      168,
    );

    expect(report.rangeHours).toBe(168);
    expect(report.eventCount).toBe(0);
    expect(report.repeatOffenders).toEqual([]);
  });
});
