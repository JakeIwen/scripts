import { useState } from 'react';

import { usePollingResource } from '../../hooks/usePollingResource';
import { useSingleFlightAction } from '../../hooks/useSingleFlightAction';
import { AlarmEditor } from './AlarmEditor';
import {
  deleteAlarm,
  fetchAlarmFavorites,
  fetchAlarms,
  repeatLabel,
  saveAlarm,
  setAlarmEnabled,
  type SonosAlarm,
} from './alarms';
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

export function SonosAlarms() {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="sonos-alarm-section">
      <button
        className="secondary-button"
        type="button"
        aria-expanded={expanded}
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? 'Hide alarms' : 'Alarms'}
      </button>
      {expanded && <SonosAlarmPanel />}
    </div>
  );
}

function useAlarmPanel() {
  const { running, run } = useSingleFlightAction();
  const resource = usePollingResource({ load: fetchAlarms, intervalMs: 60_000, enabled: !running });
  const favorites = usePollingResource({ load: fetchAlarmFavorites, intervalMs: 300_000 });
  const [editing, setEditing] = useState<SonosAlarm | null | undefined>(undefined);
  const [message, setMessage] = useState('');
  const [failure, setFailure] = useState(false);
  const busy = running || resource.refreshing;

  async function perform(action: () => Promise<{ message: string }>) {
    await run(async () => {
      setMessage('');
      setFailure(false);
      try {
        const result = await action();
        setMessage(result.message);
        setEditing(undefined);
      } catch (error) {
        setFailure(true);
        setMessage(error instanceof Error ? error.message : 'Alarm change failed');
        // Close uncertain/stale drafts, so create cannot be accidentally replayed.
        setEditing(undefined);
      } finally {
        await resource.refresh();
      }
    });
  }

  return { resource, favorites, editing, setEditing, message, failure, busy, perform };
}

function SonosAlarmPanel() {
  const { resource, favorites, editing, setEditing, message, failure, busy, perform } =
    useAlarmPanel();
  return (
    <section className="panel-card sonos-alarms" aria-labelledby="sonos-alarms-title">
      <AlarmPanelHeader
        busy={busy}
        addDisabled={!resource.data?.rooms.length || !!resource.error || editing !== undefined}
        localTime={resource.data?.local_time ?? null}
        onAdd={() => setEditing(null)}
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
          {editing !== undefined && (
            <AlarmEditor
              key={editing?.id ?? 'new'}
              alarm={editing}
              status={resource.data}
              favorites={favorites.data ?? []}
              busy={busy || !!resource.error}
              onCancel={() => setEditing(undefined)}
              onSave={(draft) => void perform(() => saveAlarm(draft, editing))}
            />
          )}
          {resource.data.alarms.length === 0 && <p>No Sonos alarms configured.</p>}
          {resource.data.alarms.map((alarm) => (
            <AlarmRow
              key={`${alarm.id}:${alarm.revision}`}
              alarm={alarm}
              busy={busy || !!resource.error || editing !== undefined}
              onEdit={() => setEditing(alarm)}
              onEnabled={(enabled) => void perform(() => setAlarmEnabled(alarm, enabled))}
              onDelete={() => void perform(() => deleteAlarm(alarm))}
            />
          ))}
        </>
      )}
    </section>
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
        <h3 id="sonos-alarms-title">Alarms</h3>
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
