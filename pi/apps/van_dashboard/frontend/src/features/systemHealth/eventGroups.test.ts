import { describe, expect, it } from 'vitest';

import { groupSystemHealthEvents } from './eventGroups';
import type { SystemHealthEvent } from './types';

describe('system health event grouping', () => {
  it('combines nonconsecutive repeats while preserving timestamps, devices, and severity', () => {
    const event: SystemHealthEvent = {
      timestamp: 10,
      category: 'usb',
      severity: 'warning',
      summary: 'USB communication failure',
      message: 'usb 1-2: descriptor read, error -32',
    };
    const events = [
      event,
      { ...event, timestamp: 30, message: 'usb 1-3: descriptor read, error -32' },
      { ...event, timestamp: 50 },
      { ...event, timestamp: 20, severity: 'critical' as const },
      { ...event, timestamp: 50 },
    ];
    const groups = groupSystemHealthEvents(events);
    expect(groups).toHaveLength(3);
    expect(groups[0]).toMatchObject({ timestamps: [50, 50, 10], firstSeen: 10, lastSeen: 50 });
    expect(groups[1]?.event.message).toContain('usb 1-3:');
    expect(groups[2]?.event.severity).toBe('critical');
    expect(events[0]?.timestamp).toBe(10);
    expect(groupSystemHealthEvents([])).toEqual([]);
  });
});
