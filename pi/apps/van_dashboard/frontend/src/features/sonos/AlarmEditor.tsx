import { useState } from 'react';

import {
  ALARM_DAYS,
  ALARM_REPEATS,
  type AlarmDraft,
  type AlarmFavorite,
  type AlarmStatus,
  type SonosAlarm,
} from './alarms';

interface Props {
  alarm: SonosAlarm | null;
  status: AlarmStatus;
  favorites: AlarmFavorite[];
  busy: boolean;
  onSave: (draft: AlarmDraft) => void;
  onCancel: () => void;
}

function initialDraft(alarm: SonosAlarm | null, room: string): AlarmDraft {
  return {
    room: alarm?.room ?? room,
    start_time: alarm?.start_time ?? '07:00:00',
    duration: alarm?.duration ?? '01:00:00',
    recurrence: alarm?.recurrence ?? 'DAILY',
    enabled: alarm?.enabled ?? false,
    volume: alarm?.volume ?? 20,
    include_linked_zones: alarm?.include_linked_zones ?? false,
    play_mode: alarm?.play_mode ?? 'NORMAL',
    sound: alarm ? 'keep' : 'chime',
  };
}

function RepeatEditor({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const custom = value.startsWith('ON_');
  return (
    <>
      <label>
        Repeat
        <select
          value={custom ? 'custom' : value}
          onChange={(e) => onChange(e.target.value === 'custom' ? 'ON_' : e.target.value)}
        >
          {Object.entries(ALARM_REPEATS).map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
          <option value="custom">Choose days</option>
        </select>
      </label>
      {custom && (
        <fieldset className="sonos-alarm-days">
          <legend>Repeat days</legend>
          {ALARM_DAYS.map((day, index) => (
            <label key={day}>
              <input
                type="checkbox"
                checked={value.slice(3).includes(String(index))}
                onChange={(e) => {
                  const days = new Set(value.slice(3));
                  if (e.target.checked) days.add(String(index));
                  else days.delete(String(index));
                  onChange(`ON_${[...days].sort().join('')}`);
                }}
              />
              {day}
            </label>
          ))}
          {value === 'ON_' && <small>Select at least one day.</small>}
        </fieldset>
      )}
    </>
  );
}

export function AlarmEditor({ alarm, status, favorites, busy, onSave, onCancel }: Props) {
  const [draft, setDraft] = useState(() => initialDraft(alarm, status.rooms[0]?.id ?? ''));
  const update = <K extends keyof AlarmDraft>(key: K, value: AlarmDraft[K]) =>
    setDraft((old) => ({ ...old, [key]: value }));
  return (
    <form
      className="panel-card sonos-alarm-editor"
      onSubmit={(e) => {
        e.preventDefault();
        onSave(draft);
      }}
    >
      <h4>{alarm ? 'Edit alarm' : 'New alarm'}</h4>
      <fieldset disabled={busy} className="sonos-alarm-fields">
        <ScheduleFields draft={draft} update={update} status={status} />
        <SoundFields
          draft={draft}
          update={update}
          alarm={alarm}
          favorites={favorites}
          status={status}
        />
        <DurationFields draft={draft} update={update} />
      </fieldset>
      <div className="sonos-alarm-actions">
        <button
          className="primary-button"
          type="submit"
          disabled={
            busy || draft.recurrence === 'ON_' || !status.rooms.some((r) => r.id === draft.room)
          }
        >
          {busy ? 'Saving…' : 'Save alarm'}
        </button>
        <button className="secondary-button" type="button" disabled={busy} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

interface FieldProps {
  draft: AlarmDraft;
  update: <K extends keyof AlarmDraft>(key: K, value: AlarmDraft[K]) => void;
}

function ScheduleFields({ draft, update, status }: FieldProps & Pick<Props, 'status'>) {
  return (
    <>
      <label>
        Time
        <span className="sonos-alarm-time-input">
          <input
            type="time"
            aria-label={status.time_zone ? `Time (${status.time_zone})` : 'Time'}
            step="1"
            required
            value={draft.start_time}
            onChange={(e) => update('start_time', e.target.value)}
          />
          {status.time_zone && <span>{status.time_zone}</span>}
        </span>
      </label>
      <label>
        Speaker
        <select required value={draft.room} onChange={(e) => update('room', e.target.value)}>
          {!status.rooms.some((r) => r.id === draft.room) && (
            <option value={draft.room}>Unavailable speaker</option>
          )}
          {status.rooms.map((room) => (
            <option key={room.id} value={room.id}>
              {room.name}
            </option>
          ))}
        </select>
      </label>
      <RepeatEditor value={draft.recurrence} onChange={(value) => update('recurrence', value)} />
    </>
  );
}

function SoundFields({
  draft,
  update,
  alarm,
  favorites,
  status,
}: FieldProps & Pick<Props, 'alarm' | 'favorites' | 'status'>) {
  return (
    <>
      <label>
        Sound
        <select value={draft.sound} onChange={(e) => update('sound', e.target.value)}>
          {alarm && <option value="keep">Keep current · {alarm.sound_title}</option>}
          <option value="chime">Sonos chime</option>
          {favorites.map((f) => (
            <option key={f.id} value={`favorite:${f.id}`}>
              {f.title}
            </option>
          ))}
        </select>
      </label>
      <label>
        Volume · {draft.volume}%
        <input
          type="range"
          min="0"
          max="100"
          value={draft.volume}
          onChange={(e) => update('volume', Number(e.target.value))}
        />
      </label>
      <label>
        Playback mode
        <select value={draft.play_mode} onChange={(e) => update('play_mode', e.target.value)}>
          {status.play_modes.map((mode) => (
            <option key={mode} value={mode}>
              {mode.toLowerCase().replaceAll('_', ' ')}
            </option>
          ))}
        </select>
      </label>
    </>
  );
}

function DurationFields({ draft, update }: FieldProps) {
  return (
    <>
      <label>
        Duration (hours:minutes:seconds)
        <input
          type="text"
          inputMode="text"
          placeholder="No limit"
          pattern="[0-9]{2}:[0-9]{2}(:[0-9]{2})?"
          value={draft.duration}
          onChange={(e) => update('duration', e.target.value)}
        />
        <small>Leave blank to play until stopped.</small>
      </label>
      <label className="sonos-alarm-check">
        <input
          type="checkbox"
          checked={draft.enabled}
          onChange={(e) => update('enabled', e.target.checked)}
        />
        Enabled
      </label>
      <label className="sonos-alarm-check">
        <input
          type="checkbox"
          checked={draft.include_linked_zones}
          onChange={(e) => update('include_linked_zones', e.target.checked)}
        />
        Include grouped speakers
      </label>
    </>
  );
}
