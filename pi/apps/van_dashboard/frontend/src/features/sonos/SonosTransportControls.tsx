import type { SonosControls } from './controls';
import type { SonosStatus } from './types';

const SKIP_SECONDS = 10;

interface SonosTransportControlsProps {
  status: SonosStatus;
  controls: SonosControls;
  compact?: boolean;
  /** Show ±10 s skips (full pane only); enabled when the track has a clock. */
  seekable?: boolean;
}

export function SonosTransportControls({
  status,
  controls,
  compact = false,
  seekable = false,
}: SonosTransportControlsProps) {
  const playing = status.nowPlaying.transportState === 'PLAYING';
  const classes = `sonos-transport${compact ? ' sonos-transport--tile' : ''}`;

  return (
    <div
      className={classes}
      role="group"
      aria-label="Sonos playback controls"
      aria-busy={controls.running}
    >
      <button
        type="button"
        disabled={controls.running}
        aria-label="Previous track"
        onClick={() => void controls.transport('previous')}
      >
        ⏮︎
      </button>
      {!compact && (
        <button
          className="sonos-transport__skip"
          type="button"
          disabled={controls.running || !seekable}
          aria-label={`Back ${SKIP_SECONDS} seconds`}
          onClick={() => void controls.seek(-SKIP_SECONDS)}
        >
          −{SKIP_SECONDS}
        </button>
      )}
      <button
        className="sonos-transport__play"
        type="button"
        disabled={controls.running}
        aria-label={playing ? 'Pause' : 'Play'}
        onClick={() => void controls.transport('play_pause')}
      >
        {playing ? 'Ⅱ' : '▶︎'}
      </button>
      {!compact && (
        <button
          className="sonos-transport__skip"
          type="button"
          disabled={controls.running || !seekable}
          aria-label={`Forward ${SKIP_SECONDS} seconds`}
          onClick={() => void controls.seek(SKIP_SECONDS)}
        >
          +{SKIP_SECONDS}
        </button>
      )}
      <button
        type="button"
        disabled={controls.running}
        aria-label="Next track"
        onClick={() => void controls.transport('next')}
      >
        ⏭︎
      </button>
    </div>
  );
}
