import type { SonosAlarm } from './alarms';

export interface AlarmFilters {
  day: number | null;
  enabled: boolean | null;
}

const repeatDays = new Map([
  ['DAILY', '0123456'],
  ['WEEKDAYS', '12345'],
  ['WEEKENDS', '06'],
]);

export function alarmIncludesDay(
  alarm: SonosAlarm,
  day: number,
  localTime: string | null,
): boolean {
  if (alarm.recurrence === 'ONCE') {
    if (!localTime) return false;
    // Use UTC date operations on the speaker's wall-clock text to avoid the
    // browser timezone changing which weekday a one-time alarm belongs to.
    const next = new Date(`${localTime.replace(' ', 'T')}Z`);
    if (!Number.isFinite(next.getTime())) return false;
    if (alarm.start_time <= localTime.slice(11)) next.setUTCDate(next.getUTCDate() + 1);
    return next.getUTCDay() === day;
  }
  const days = repeatDays.get(alarm.recurrence) ?? alarm.recurrence.slice(3);
  return days.includes(String(day));
}

export function filterAlarms(
  alarms: SonosAlarm[],
  filters: AlarmFilters,
  localTime: string | null,
) {
  return alarms.filter(
    (alarm) =>
      (filters.enabled === null || alarm.enabled === filters.enabled) &&
      (filters.day === null || alarmIncludesDay(alarm, filters.day, localTime)),
  );
}
