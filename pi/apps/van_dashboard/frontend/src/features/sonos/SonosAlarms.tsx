import { useId, useState } from 'react';

import type { SonosAlarmControls } from './alarmControls';
import { AlarmEditor } from './AlarmEditor';
import { deleteAlarm, repeatLabel, saveAlarm, setAlarmEnabled, type SonosAlarm } from './alarms';
import './alarms.css';

function AlarmRow({
  alarm,
  busy,
  onEdit,
  onEnabled,
  onDelete,
}: {
  alarm: SonosAlarm;
  busy: boolean;
  onEdit: () => void;
  onEnabled: (enabled: boolean) => void;
  onDelete: () => void;
}) {
  const [confirmDelete, setConfirmDelete] = useState(false);
  return (
    <article className="sonos-alarm-row">
      <div>
        <strong>
          {alarm.start_time.slice(0, 5)} · {alarm.room_name}
        </strong>
        <span>
          {repeatLabel(alarm.recurrence)} · {alarm.enabled ? 'On' : 'Off'}
        </span>
        <small>
          {alarm.sound_title} · {alarm.volume}% · {alarm.duration || 'No time limit'}
        </small>
      </div>
      <div className="sonos-alarm-actions">
        <button
          type="button"
          className="secondary-button"
          disabled={busy}
          aria-pressed={alarm.enabled}
          aria-label={`${alarm.enabled ? 'Disable' : 'Enable'} alarm at ${alarm.start_time.slice(0, 5)} on ${alarm.room_name}`}
          onClick={() => onEnabled(!alarm.enabled)}
        >
          {alarm.enabled ? 'Disable' : 'Enable'}
        </button>
        <button type="button" className="secondary-button" disabled={busy} onClick={onEdit}>
          Edit
        </button>
        <button
          type="button"
          className="secondary-button"
          disabled={busy}
          onClick={() => setConfirmDelete(true)}
        >
          Delete
        </button>
      </div>
      {confirmDelete && (
        <AlarmDeleteConfirmation
          alarm={alarm}
          busy={busy}
          onDelete={onDelete}
          onCancel={() => setConfirmDelete(false)}
        />
      )}
    </article>
  );
}

function AlarmDeleteConfirmation({
  alarm,
  busy,
  onDelete,
  onCancel,
}: {
  alarm: SonosAlarm;
  busy: boolean;
  onDelete: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="sonos-alarm-confirm">
      <span>
        Delete the {alarm.start_time.slice(0, 5)} alarm on {alarm.room_name}?
      </span>
      <button type="button" className="danger-button" disabled={busy} onClick={onDelete}>
        Confirm delete
      </button>
      <button type="button" className="secondary-button" disabled={busy} onClick={onCancel}>
        Cancel
      </button>
    </div>
  );
}

export function SonosAlarms({ controls }: { controls: SonosAlarmControls }) {
  const { expanded, setExpanded } = controls;
  const panelId = useId();
  return (
    <section className="sonos-alarm-section">
      <h3>
        <button
          className="sonos-alarm-summary"
          type="button"
          aria-expanded={expanded}
          aria-controls={panelId}
          onClick={() => setExpanded(!expanded)}
        >
          <span aria-hidden="true">{expanded ? '▾' : '▸'}</span>Alarms
        </button>
      </h3>
      {expanded && (
        <div id={panelId}>
          <SonosAlarmPanel controls={controls} />
        </div>
      )}
    </section>
  );
}

function SonosAlarmPanel({ controls }: { controls: SonosAlarmControls }) {
  const { resource, favorites, editing, setEditing, message, failure, busy, perform } = controls;
  return (
    <div className="sonos-alarms">
      <AlarmPanelHeader
        busy={busy}
        addDisabled={!resource.data?.rooms.length || !!resource.error || editing !== null}
        localTime={resource.data?.local_time ?? null}
        onAdd={() => setEditing({ alarm: null })}
        onRefresh={() => {
          void resource.refresh();
          void favorites.refresh();
        }}
      />
      {message && (
        <p role={failure ? 'alert' : 'status'} className={failure ? 'error-message' : ''}>
          {message}
        </p>
      )}
      {resource.error && (
        <p role="alert" className="error-message">
          {resource.error.message}
        </p>
      )}
      {resource.initialLoading && <p>Loading alarms…</p>}
      {favorites.error && <p>{favorites.error.message}</p>}
      {resource.data && (
        <>
          {editing !== null && (
            <AlarmEditor
              key={editing.alarm?.id ?? 'new'}
              alarm={editing.alarm}
              status={resource.data}
              favorites={favorites.data ?? []}
              busy={busy || !!resource.error}
              onCancel={() => setEditing(null)}
              onSave={(draft) => void perform(() => saveAlarm(draft, editing.alarm))}
            />
          )}
          {resource.data.alarms.length === 0 && <p>No Sonos alarms configured.</p>}
          {resource.data.alarms.map((alarm) => (
            <AlarmRow
              key={`${alarm.id}:${alarm.revision}`}
              alarm={alarm}
              busy={busy || !!resource.error || editing !== null}
              onEdit={() => setEditing({ alarm })}
              onEnabled={(enabled) => void perform(() => setAlarmEnabled(alarm, enabled))}
              onDelete={() => void perform(() => deleteAlarm(alarm))}
            />
          ))}
        </>
      )}
    </div>
  );
}

function AlarmPanelHeader({
  busy,
  addDisabled,
  localTime,
  onAdd,
  onRefresh,
}: {
  busy: boolean;
  addDisabled: boolean;
  localTime: string | null;
  onAdd: () => void;
  onRefresh: () => void;
}) {
  return (
    <>
      <header>
        <div className="sonos-alarm-actions">
          <button className="secondary-button" type="button" disabled={busy} onClick={onRefresh}>
            Refresh alarms
          </button>
          <button
            className="secondary-button"
            type="button"
            disabled={busy || addDisabled}
            onClick={onAdd}
          >
            Add alarm
          </button>
        </div>
      </header>
      <p>
        Times follow the Sonos system’s time zone.
        {localTime ? ` Speaker clock: ${localTime}.` : ''}
      </p>
    </>
  );
}
