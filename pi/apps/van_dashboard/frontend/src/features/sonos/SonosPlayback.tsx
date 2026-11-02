import { useId, useState } from 'react';

import type { SonosControls } from './controls';
import { loopRegion, useLoopDraft } from './loop';
import { formatTrackClock } from './progress';
import { SonosLoopPanel } from './SonosLoopPanel';
import { SonosSeekBar } from './SonosSeekBar';
import { SonosTransportControls } from './SonosTransportControls';
import type { SonosStatus, SonosTrackProgress } from './types';

function LoopIcon() {
  return (
    <svg className="sonos-loop-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4z" />
    </svg>
  );
}

export function SonosPlayback({
  status,
  progress,
  controls,
}: {
  status: SonosStatus;
  progress: SonosTrackProgress | null;
  controls: SonosControls;
}) {
  const [loopOpen, setLoopOpen] = useState(false);
  const panelId = useId();
  const { loop } = status;
  const draft = useLoopDraft(loop);
  const durationSeconds = status.nowPlaying.durationSeconds;
  const loopAvailable = progress !== null || loop.active;
  const region = loopRegion(loop, draft, loopOpen, durationSeconds);

  // The panel is a sibling so the sheet header can give it its own grid row.
  return (
    <>
      <div className="sonos-now-playing">
        <div className="sonos-now-playing__controls">
          <SonosTransportControls
            status={status}
            controls={controls}
            seekable={progress !== null}
          />
          <button
            className={`sonos-loop-toggle${loop.active ? ' is-active' : ''}`}
            type="button"
            disabled={!loopAvailable}
            aria-label={loop.active ? 'Loop (running)' : 'Loop'}
            aria-expanded={loopOpen}
            aria-controls={panelId}
            onClick={() => setLoopOpen((open) => !open)}
          >
            <LoopIcon />
          </button>
        </div>
        <div className="sonos-now-playing__copy">
          {progress && <SonosSeekBar progress={progress} onSeek={controls.seekTo} loop={region} />}
          <small className="sonos-track-caption">
            <span>
              {[status.nowPlaying.artist, status.nowPlaying.title].filter(Boolean).join(' · ')}
            </span>
            {progress && <span className="sonos-track-time">{progress.label}</span>}
            {loop.active && (
              <span className="sonos-track-time sonos-track-loop">
                <LoopIcon />
                <span className="visually-hidden">Loop</span> {formatTrackClock(loop.startSeconds)}–
                {formatTrackClock(loop.endSeconds)}
              </span>
            )}
          </small>
        </div>
      </div>
      {loopOpen && loopAvailable && (
        <SonosLoopPanel
          id={panelId}
          loop={loop}
          draft={draft}
          positionSeconds={progress?.positionSeconds ?? null}
          durationSeconds={durationSeconds}
          controls={controls}
        />
      )}
    </>
  );
}
