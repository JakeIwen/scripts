import { Tile } from '../../components/Tile';
import type { SonosControls } from './controls';
import { SonosTransportControls } from './SonosTransportControls';
import type { SonosStatus, SonosTrackProgress } from './types';
import './sonos.css';

export interface SonosTileProps {
  status: SonosStatus | null;
  error: Error | null;
  refreshing: boolean;
  progress: SonosTrackProgress | null;
  controls: SonosControls;
  onOpen: () => void;
}

export function SonosTile({
  status,
  error,
  refreshing,
  progress,
  controls,
  onOpen,
}: SonosTileProps) {
  if (!status) {
    return (
      <Tile
        icon="🔊"
        title="Sonos"
        summary={error?.message ?? 'Finding speakers and current playback…'}
        status={
          <button className="sonos-tile__speaker-summary" type="button" onClick={onOpen}>
            {error ? 'Unavailable' : 'Finding…'}
          </button>
        }
        tone={error ? 'bad' : 'neutral'}
        className="sonos-tile"
      >
        <button
          className="sonos-tile__open"
          type="button"
          aria-label="Open Sonos details"
          onClick={onOpen}
        />
      </Tile>
    );
  }

  const groupedCount = status.speakers.filter((speaker) => speaker.grouped).length;
  const artist = status.nowPlaying.artist || status.nowPlaying.album || status.coordinator;
  const albumArt = status.nowPlaying.albumArt;

  return (
    <Tile
      icon="🔊"
      title="Sonos"
      summary={
        <span className="sonos-tile__track">
          <strong>{status.nowPlaying.title}</strong>
          <small>{artist}</small>
        </span>
      }
      status={
        <button className="sonos-tile__speaker-summary" type="button" onClick={onOpen}>
          {status.coordinator} · {groupedCount}/{status.speakers.length}
        </button>
      }
      tone="neutral"
      className={`sonos-tile ${albumArt ? 'has-art' : ''}`}
      style={
        albumArt
          ? {
              backgroundImage: `linear-gradient(90deg,#111b22ed 0%,#111b22c7 58%,#111b226b 100%),url("${albumArt}")`,
            }
          : undefined
      }
    >
      <button
        className="sonos-tile__open"
        type="button"
        aria-label="Open Sonos details"
        onClick={onOpen}
      />
      <SonosTransportControls status={status} controls={controls} compact />
      {progress && (
        <div className="sonos-progress" aria-label={`Track progress: ${progress.label}`}>
          <span style={{ width: `${progress.percent}%` }} />
        </div>
      )}
      {error && <p className="sonos-stale-error">Refresh failed · {error.message}</p>}
      {refreshing && <p className="sonos-control-hint">Refreshing playback…</p>}
    </Tile>
  );
}
