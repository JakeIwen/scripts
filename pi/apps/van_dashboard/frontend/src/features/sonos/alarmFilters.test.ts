import { describe, expect, it } from 'vitest';

import { alarmIncludesDay, filterAlarms } from './alarmFilters';
import type { SonosAlarm } from './alarms';

const alarm: SonosAlarm = {
  id: '1',
  revision: 'r1',
  room: 'front',
  room_name: 'Front',
  start_time: '07:00:00',
  duration: '',
  recurrence: 'ON_05',
  enabled: true,
  volume: 20,
  include_linked_zones: false,
  play_mode: 'NORMAL',
  sound_title: 'Sonos chime',
};

describe('alarm filters', () => {
  it.each([
    ['DAILY', 0, true],
    ['WEEKDAYS', 0, false],
    ['WEEKDAYS', 1, true],
    ['WEEKENDS', 6, true],
    ['WEEKENDS', 3, false],
    ['ON_05', 0, true],
    ['ON_05', 1, false],
  ])('matches %s on day %s', (recurrence, day, expected) => {
    expect(alarmIncludesDay({ ...alarm, recurrence }, day, null)).toBe(expected);
  });

  it('uses the next one-time occurrence in speaker local time', () => {
    const once = { ...alarm, recurrence: 'ONCE' };
    expect(alarmIncludesDay(once, 0, '2026-10-17 23:00:00')).toBe(true);
    expect(alarmIncludesDay(once, 6, '2026-10-17 06:00:00')).toBe(true);
    expect(alarmIncludesDay(once, 0, '2026-10-17 07:00:00')).toBe(true);
    expect(alarmIncludesDay(once, 0, null)).toBe(false);
  });

  it('combines weekday and enabled filters without changing alarm records', () => {
    const disabled = { ...alarm, id: '2', enabled: false };
    const alarms = [alarm, disabled];
    expect(filterAlarms(alarms, { day: null, enabled: null }, null)).toEqual(alarms);
    expect(filterAlarms(alarms, { day: 0, enabled: false }, null)).toEqual([disabled]);
    expect(filterAlarms(alarms, { day: 1, enabled: true }, null)).toEqual([]);
    expect(alarms).toEqual([alarm, disabled]);
  });
});
