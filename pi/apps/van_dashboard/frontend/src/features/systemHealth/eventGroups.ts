import type { SystemHealthEvent } from './types';

export interface SystemHealthEventGroup {
  key: string;
  event: SystemHealthEvent;
  timestamps: number[];
  firstSeen: number;
  lastSeen: number;
}

/** Group exact repeats in the loaded timeline, preserving device/error details. */
export function groupSystemHealthEvents(events: SystemHealthEvent[]): SystemHealthEventGroup[] {
  const groups = new Map<string, SystemHealthEventGroup>();
  for (const event of events) {
    const key = JSON.stringify([event.category, event.severity, event.summary, event.message]);
    const existing = groups.get(key);
    if (existing) {
      existing.timestamps.push(event.timestamp);
      existing.firstSeen = Math.min(existing.firstSeen, event.timestamp);
      existing.lastSeen = Math.max(existing.lastSeen, event.timestamp);
    } else {
      groups.set(key, {
        key,
        event,
        timestamps: [event.timestamp],
        firstSeen: event.timestamp,
        lastSeen: event.timestamp,
      });
    }
  }
  return [...groups.values()]
    .map((group) => ({ ...group, timestamps: group.timestamps.sort((a, b) => b - a) }))
    .sort((a, b) => b.lastSeen - a.lastSeen);
}
