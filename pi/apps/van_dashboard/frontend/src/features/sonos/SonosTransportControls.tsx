import type { SonosControls } from './controls';
import type { SonosStatus } from './types';

interface SonosTransportControlsProps {
  status: SonosStatus;
  controls: SonosControls;
  compact?: boolean;
}

export function SonosTransportControls({
  status,
  controls,
  compact = false,
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
      <button
        className="sonos-transport__play"
        type="button"
        disabled={controls.running}
        aria-label={playing ? 'Pause' : 'Play'}
        onClick={() => void controls.transport('play_pause')}
      >
        {playing ? 'Ⅱ' : '▶︎'}
      </button>
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
