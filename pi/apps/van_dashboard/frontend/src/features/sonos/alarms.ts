import * as v from 'valibot';

import { getJson, postForm } from '../../api/client';
import { decode, object, text, trueValue } from '../../api/schema';

const clock = v.pipe(text, v.regex(/^([01]\d|2[0-3]):[0-5]\d:[0-5]\d$/));
const recurrence = v.pipe(text, v.regex(/^(ONCE|DAILY|WEEKDAYS|WEEKENDS|ON_[0-6]{1,7})$/));
const volume = v.pipe(v.number(), v.integer(), v.minValue(0), v.maxValue(100));
const alarmSchema = object({
  id: text,
  revision: text,
  room: text,
  room_name: text,
  start_time: clock,
  duration: v.union([v.literal(''), clock]),
  recurrence,
  enabled: v.boolean(),
  volume,
  include_linked_zones: v.boolean(),
  play_mode: text,
  sound_title: text,
});
const alarmsSchema = object({
  ok: trueValue,
  alarms: v.array(alarmSchema),
  rooms: v.array(object({ id: text, name: text })),
  local_time: v.nullable(text),
  time_zone: v.optional(v.nullable(text), null),
  play_modes: v.array(text),
});
const favoritesSchema = object({
  ok: trueValue,
  favorites: v.array(object({ id: text, title: text })),
});
const resultSchema = object({ ok: trueValue, id: text, message: text });

export type SonosAlarm = v.InferOutput<typeof alarmSchema>;
export type AlarmStatus = v.InferOutput<typeof alarmsSchema>;
export type AlarmFavorite = v.InferOutput<typeof favoritesSchema>['favorites'][number];
export type AlarmDraft = Pick<
  SonosAlarm,
  | 'room'
  | 'start_time'
  | 'duration'
  | 'recurrence'
  | 'enabled'
  | 'volume'
  | 'include_linked_zones'
  | 'play_mode'
> & { sound: string };

export const ALARM_DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'] as const;
export const ALARM_REPEATS = {
  ONCE: 'Once',
  DAILY: 'Every day',
  WEEKDAYS: 'Weekdays',
  WEEKENDS: 'Weekends',
};

export function repeatLabel(value: string): string {
  if (value in ALARM_REPEATS) return ALARM_REPEATS[value as keyof typeof ALARM_REPEATS];
  return [...value.slice(3)].map((day) => ALARM_DAYS[Number(day)]).join(', ');
}

export async function fetchAlarms(signal: AbortSignal): Promise<AlarmStatus> {
  return decode(alarmsSchema, await getJson('/api/speakers/alarms', signal), 'Sonos alarms');
}

export async function fetchAlarmFavorites(signal: AbortSignal): Promise<AlarmFavorite[]> {
  return decode(
    favoritesSchema,
    await getJson('/api/speakers/alarm-favorites', signal),
    'Sonos favorites',
  ).favorites;
}

export async function saveAlarm(draft: AlarmDraft, alarm: SonosAlarm | null) {
  const payload = await postForm('/api/speakers/alarms/save', {
    ...draft,
    id: alarm?.id ?? '',
    revision: alarm?.revision ?? '',
    enabled: draft.enabled ? '1' : '0',
    include_linked_zones: draft.include_linked_zones ? '1' : '0',
  });
  return decode(resultSchema, payload, 'Sonos alarm save');
}

export async function setAlarmEnabled(alarm: SonosAlarm, enabled: boolean) {
  const payload = await postForm('/api/speakers/alarms/enabled', {
    id: alarm.id,
    revision: alarm.revision,
    enabled: enabled ? '1' : '0',
  });
  return decode(resultSchema, payload, 'Sonos alarm change');
}

export async function deleteAlarm(alarm: SonosAlarm) {
  const payload = await postForm('/api/speakers/alarms/delete', {
    id: alarm.id,
    revision: alarm.revision,
  });
  return decode(resultSchema, payload, 'Sonos alarm change');
}
