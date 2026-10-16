import type { SonosControls } from './controls';
import { SonosTransportControls } from './SonosTransportControls';
import type { SonosStatus, SonosTrackProgress } from './types';

export function SonosPlayback({
  status,
  progress,
  controls,
}: {
  status: SonosStatus;
  progress: SonosTrackProgress | null;
  controls: SonosControls;
}) {
  return (
    <div className="sonos-now-playing">
      <SonosTransportControls status={status} controls={controls} />
      <div className="sonos-now-playing__copy">
        {progress && (
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
        )}
        <small className="sonos-track-caption">
          <span>
            {[status.nowPlaying.artist, status.nowPlaying.title].filter(Boolean).join(' · ')}
          </span>
          {progress && <span className="sonos-track-time">{progress.label}</span>}
        </small>
      </div>
    </div>
  );
}
