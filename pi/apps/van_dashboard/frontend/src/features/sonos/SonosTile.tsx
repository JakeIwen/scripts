import { StatusPill, type StatusTone } from '../../components/StatusPill';
import { Tile } from '../../components/Tile';
import type { SonosStatus, SonosTrackProgress } from './types';
import './sonos.css';

export interface SonosTileProps {
  status: SonosStatus | null;
  error: Error | null;
  refreshing: boolean;
  progress: SonosTrackProgress | null;
  onOpen: () => void;
}

function transportPresentation(status: SonosStatus): {
  label: string;
  tone: StatusTone;
} {
  switch (status.nowPlaying.transportState) {
    case 'PLAYING':
      return { label: 'Playing', tone: 'good' };
    case 'PAUSED_PLAYBACK':
      return { label: 'Paused', tone: 'warning' };
    case 'TRANSITIONING':
      return { label: 'Changing', tone: 'warning' };
    case 'STOPPED':
    case 'NO_MEDIA_PRESENT':
      return { label: 'Stopped', tone: 'neutral' };
    case 'UNKNOWN':
      return { label: 'Unknown', tone: 'neutral' };
  }
}

export function SonosTile({ status, error, refreshing, progress, onOpen }: SonosTileProps) {
  if (!status) {
    return (
      <Tile
        icon="🔊"
        title="Sonos"
        summary={error?.message ?? 'Finding speakers and current playback…'}
        status={
          <StatusPill tone={error ? 'bad' : 'neutral'}>{error ? 'No data' : 'Loading'}</StatusPill>
        }
        tone={error ? 'bad' : 'neutral'}
        onClick={onOpen}
        ariaLabel="Open Sonos details"
        className="sonos-tile"
      >
        <p className="sonos-control-hint">Open for playback and speaker controls</p>
      </Tile>
    );
  }

  const transport = transportPresentation(status);
  const groupedCount = status.speakers.filter((speaker) => speaker.grouped).length;
  const artist = status.nowPlaying.artist || status.nowPlaying.album || status.coordinator;

  return (
    <Tile
      icon="🔊"
      title="Sonos"
      summary={
        <span className="sonos-tile__track">
          <span>
            <strong>{status.nowPlaying.title}</strong>
            <small>{artist}</small>
          </span>
        </span>
      }
      status={<StatusPill tone={transport.tone}>{transport.label}</StatusPill>}
      tone={transport.tone}
      onClick={onOpen}
      ariaLabel="Open Sonos details"
      className="sonos-tile"
    >
      {progress && (
        <div className="sonos-progress" aria-label={`Track progress: ${progress.label}`}>
          <span style={{ width: `${progress.percent}%` }} />
        </div>
      )}
      <div className="sonos-tile__group">
        <span>{status.coordinator}</span>
        <strong>
          {groupedCount}/{status.speakers.length} speakers
        </strong>
      </div>
      {error && <p className="sonos-stale-error">Refresh failed · {error.message}</p>}
      <p className="sonos-control-hint">
        {refreshing ? 'Refreshing playback…' : 'Open for playback and speaker controls'}
      </p>
    </Tile>
  );
}
