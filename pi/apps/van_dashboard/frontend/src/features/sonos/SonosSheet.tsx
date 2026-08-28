import { useEffect, useState } from 'react';

import { BottomSheet } from '../../components/BottomSheet';
import type { SonosControls } from './controls';
import type { SonosSpeaker, SonosStatus, SonosTrackProgress } from './types';
import './sonos.css';

export interface SonosSheetProps {
  open: boolean;
  onClose: () => void;
  status: SonosStatus | null;
  error: Error | null;
  refreshing: boolean;
  progress: SonosTrackProgress | null;
  onRefresh: () => Promise<SonosStatus | null>;
  controls: SonosControls;
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

function TransportControls({ status, controls }: { status: SonosStatus; controls: SonosControls }) {
  const playing = status.nowPlaying.transportState === 'PLAYING';
  return (
    <div className="sonos-transport" aria-label="Playback controls" aria-busy={controls.running}>
      <button
        type="button"
        disabled={controls.running}
        aria-label="Previous track"
        onClick={() => void controls.transport('previous')}
      >
        ⏮
      </button>
      <button
        type="button"
        disabled={controls.running}
        aria-label={playing ? 'Pause' : 'Play'}
        onClick={() => void controls.transport('play_pause')}
      >
        {playing ? 'Ⅱ' : '▶'}
      </button>
      <button
        type="button"
        disabled={controls.running}
        aria-label="Next track"
        onClick={() => void controls.transport('next')}
      >
        ⏭
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
  progress,
  onRefresh,
  controls,
}: SonosSheetProps) {
  return (
    <BottomSheet
      open={open}
      title="Sonos"
      description="Playback, active-group, volume, mute, grouping, and coordinator controls."
      onClose={onClose}
    >
      <div className="sonos-sheet__toolbar">
        <aside className="sonos-control-panel" aria-live="polite">
          <strong>{controls.running ? 'Applying Sonos control…' : 'Controls enabled'}</strong>
          <span>
            {controls.lastError ??
              controls.lastMessage ??
              'Every control is reconciled with an authoritative speaker refresh.'}
          </span>
        </aside>
        <button
          className="secondary-button"
          type="button"
          disabled={refreshing || controls.running || !open}
          onClick={() => void onRefresh()}
        >
          {refreshing ? 'Refreshing…' : 'Refresh status'}
        </button>
      </div>

      {error && <p className="error-message">{error.message}</p>}
      {!status ? (
        <p className="sonos-sheet__empty">
          {error ? 'Sonos is unavailable.' : 'Finding speakers…'}
        </p>
      ) : (
        <div className="section-stack">
          <section className="panel-card sonos-now-playing">
            <div className="sonos-now-playing__copy">
              <span>Now playing</span>
              <h3>{status.nowPlaying.title}</h3>
              <p>
                {status.nowPlaying.artist || status.coordinator}
                {status.nowPlaying.album ? ` · ${status.nowPlaying.album}` : ''}
              </p>
              {progress && (
                <>
                  <div
                    className="sonos-progress"
                    role="progressbar"
                    aria-valuemin={0}
                    aria-valuemax={progress.durationSeconds}
                    aria-valuenow={Math.round(progress.positionSeconds)}
                    aria-valuetext={progress.label}
                  >
                    <span style={{ width: `${progress.percent}%` }} />
                  </div>
                  <small>{progress.label}</small>
                </>
              )}
            </div>
            <TransportControls status={status} controls={controls} />
          </section>

          <section className="panel-card sonos-group">
            <header>
              <div>
                <h3>Active group</h3>
                <p>Coordinator · {status.coordinator}</p>
              </div>
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
            </header>
            <div className="sonos-group__volume">
              <span>Group volume</span>
              <VolumeEditor
                label="Group"
                value={status.group.volume}
                disabled={controls.running}
                onCommit={(volume) => void controls.setGroupVolume(volume)}
              />
            </div>
          </section>

          <section className="sonos-speakers" aria-labelledby="sonos-speakers-title">
            <h3 id="sonos-speakers-title">Speakers</h3>
            <div className="sonos-speaker-list">
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
        </div>
      )}
    </BottomSheet>
  );
}
