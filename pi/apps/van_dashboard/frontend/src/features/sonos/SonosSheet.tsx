import { useEffect, useState, type ReactNode } from 'react';

import { BottomSheet, UpdatedAge } from '../../components/BottomSheet';
import type { SonosControls } from './controls';
import { SonosPlayback } from './SonosPlayback';
import type { SonosSpeaker, SonosStatus, SonosTrackProgress } from './types';
import './sonos.css';

export interface SonosSheetProps {
  open: boolean;
  onClose: () => void;
  status: SonosStatus | null;
  error: Error | null;
  refreshing: boolean;
  updatedAt: number | null;
  progress: SonosTrackProgress | null;
  onRefresh: () => Promise<SonosStatus | null>;
  controls: SonosControls;
  alarms: ReactNode;
}

function speakerDetail(speaker: SonosSpeaker, coordinator: string): string {
  if (speaker.coordinator) return 'Active coordinator';
  if (speaker.grouped) return `Grouped with ${coordinator}`;
  return `Separate group · ${speaker.groupCoordinator}`;
}

interface VolumeEditorProps {
  label: string;
  value: number | null;
  disabled: boolean;
  onCommit: (volume: number) => void;
}

function VolumeEditor({ label, value, disabled, onCommit }: VolumeEditorProps) {
  const [draft, setDraft] = useState(value ?? 0);

  useEffect(() => {
    setDraft(value ?? 0);
  }, [value]);

  const unavailable = value === null;
  return (
    <div className="sonos-volume-editor">
      <input
        type="range"
        min={0}
        max={100}
        value={draft}
        disabled={disabled || unavailable}
        aria-label={`${label} volume`}
        onChange={(event) => setDraft(Number(event.currentTarget.value))}
      />
      <output>{unavailable ? '—' : `${draft}%`}</output>
      <button
        className="secondary-button sonos-volume-editor__apply"
        type="button"
        disabled={disabled || unavailable || draft === value}
        aria-label={`Set ${label} volume`}
        onClick={() => onCommit(draft)}
      >
        Set
      </button>
    </div>
  );
}

function SpeakerRow({
  speaker,
  coordinator,
  controls,
}: {
  speaker: SonosSpeaker;
  coordinator: string;
  controls: SonosControls;
}) {
  const muteKnown = speaker.muted !== null;
  return (
    <article className="sonos-speaker">
      <input
        className="sonos-speaker__group"
        type="checkbox"
        checked={speaker.grouped}
        disabled={controls.running || speaker.coordinator}
        aria-label={`Group ${speaker.name} with ${coordinator}`}
        onChange={(event) => void controls.setGrouping(speaker.name, event.currentTarget.checked)}
      />
      <div className="sonos-speaker__identity">
        <strong>{speaker.name}</strong>
        <small>{speakerDetail(speaker, coordinator)}</small>
      </div>
      <button
        className="sonos-mute-button"
        type="button"
        disabled={controls.running || !muteKnown}
        aria-label={`${speaker.muted ? 'Unmute' : 'Mute'} ${speaker.name}`}
        aria-pressed={speaker.muted === true}
        onClick={() => void controls.setSpeakerMuted(speaker.name, !speaker.muted)}
      >
        {speaker.muted ? '🔇' : '🔊'}
      </button>
      <button
        className="secondary-button sonos-select-button"
        type="button"
        disabled={controls.running || speaker.grouped}
        aria-pressed={speaker.coordinator}
        onClick={() => void controls.selectCoordinator(speaker.name)}
      >
        {speaker.coordinator ? 'Selected' : speaker.grouped ? 'Active group' : 'Select group'}
      </button>
      <VolumeEditor
        label={speaker.name}
        value={speaker.volume}
        disabled={controls.running}
        onCommit={(volume) => void controls.setSpeakerVolume(speaker.name, volume)}
      />
    </article>
  );
}

export function SonosSheet({
  open,
  onClose,
  status,
  error,
  refreshing,
  updatedAt,
  progress,
  onRefresh,
  controls,
  alarms,
}: SonosSheetProps) {
  return (
    <BottomSheet
      open={open}
      title="Sonos"
      onClose={onClose}
      className="sonos-sheet"
      headerContent={
        status && <SonosPlayback status={status} progress={progress} controls={controls} />
      }
      headerActions={
        <>
          <UpdatedAge timestamp={updatedAt === null ? null : updatedAt / 1000} active={open} />
          <button
            className="icon-button"
            type="button"
            aria-label="Refresh status"
            title="Refresh status"
            disabled={refreshing || controls.running || !open}
            onClick={() => void onRefresh()}
          >
            ↻
          </button>
        </>
      }
    >
      {error && <p className="error-message">{error.message}</p>}
      {controls.lastError && (
        <p className="error-message" role="alert">
          {controls.lastError}
        </p>
      )}
      {!status ? (
        <p className="sonos-sheet__empty">
          {error ? 'Sonos is unavailable.' : 'Finding speakers…'}
        </p>
      ) : (
        <section className="sonos-speakers" aria-labelledby="sonos-speakers-title">
          <h3 id="sonos-speakers-title">Speakers</h3>
          <div className="sonos-speaker-list">
            <GroupVolume status={status} controls={controls} />
            {status.speakers.map((speaker) => (
              <SpeakerRow
                speaker={speaker}
                coordinator={status.coordinator}
                controls={controls}
                key={speaker.name}
              />
            ))}
          </div>
        </section>
      )}
      {open && alarms}
    </BottomSheet>
  );
}

function GroupVolume({ status, controls }: { status: SonosStatus; controls: SonosControls }) {
  return (
    <div className="sonos-group" role="group" aria-label="Active speaker group">
      <button
        className="sonos-mute-button"
        type="button"
        disabled={controls.running || status.group.muted === null}
        aria-label={status.group.muted ? 'Unmute group' : 'Mute group'}
        aria-pressed={status.group.muted === true}
        onClick={() => void controls.setGroupMuted(!status.group.muted)}
      >
        {status.group.muted ? '🔇' : '🔊'}
      </button>
      <VolumeEditor
        label="Group"
        value={status.group.volume}
        disabled={controls.running}
        onCommit={(volume) => void controls.setGroupVolume(volume)}
      />
    </div>
  );
}
