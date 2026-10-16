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
      <div className="sonos-now-playing__copy">
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
      <SonosTransportControls status={status} controls={controls} />
    </div>
  );
}
