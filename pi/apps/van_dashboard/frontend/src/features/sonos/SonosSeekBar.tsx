import { useEffect, useRef, useState } from 'react';

import type { LoopRegion } from './loop';
import { formatTrackClock } from './progress';
import type { SonosTrackProgress } from './types';
import './seek.css';

interface SonosSeekBarProps {
  progress: SonosTrackProgress;
  onSeek: (positionSeconds: number) => Promise<unknown>;
  loop?: LoopRegion | null;
}

function percent(seconds: number, durationSeconds: number): string {
  return `${(seconds / durationSeconds) * 100}%`;
}

/**
 * The track bar as a scrubber: the drawn bar stays the visual, and a native
 * range input with only its thumb showing sits over it for pointer, touch and
 * keyboard input. The seek is sent once, when the drag is released.
 */
export function SonosSeekBar({ progress, onSeek, loop = null }: SonosSeekBarProps) {
  const input = useRef<HTMLInputElement>(null);
  const [scrubSeconds, setScrubSeconds] = useState<number | null>(null);

  useEffect(() => {
    const element = input.current;
    if (!element) return;
    // React's onChange fires at every drag step; the native change event
    // fires once, when the drag or key press is released.
    const release = () => {
      const target = Number(element.value);
      void onSeek(target).finally(() => {
        // Keep showing a newer drag that started while this seek was in flight.
        setScrubSeconds((current) => (current === target ? null : current));
      });
    };
    element.addEventListener('change', release);
    return () => element.removeEventListener('change', release);
  }, [onSeek]);

  const { durationSeconds } = progress;
  const seconds = scrubSeconds ?? progress.positionSeconds;
  const scrubbing = scrubSeconds !== null;

  return (
    <div className={`sonos-seek${scrubbing ? ' is-scrubbing' : ''}`}>
      <div className="sonos-progress" aria-hidden="true">
        <span style={{ width: percent(seconds, durationSeconds) }} />
        {loop && (
          <i
            className={`sonos-progress__loop${loop.draft ? ' is-draft' : ''}`}
            style={{
              left: percent(loop.start, durationSeconds),
              width: percent(loop.end - loop.start, durationSeconds),
            }}
          />
        )}
      </div>
      <input
        ref={input}
        className="sonos-seek__input"
        type="range"
        min={0}
        max={durationSeconds}
        step={1}
        value={Math.floor(seconds)}
        aria-label="Track position"
        aria-valuetext={`${formatTrackClock(seconds)} of ${formatTrackClock(durationSeconds)}`}
        onChange={(event) => setScrubSeconds(Number(event.currentTarget.value))}
      />
      {scrubbing && (
        <output className="sonos-seek__time" style={{ left: percent(seconds, durationSeconds) }}>
          {formatTrackClock(seconds)}
        </output>
      )}
    </div>
  );
}
