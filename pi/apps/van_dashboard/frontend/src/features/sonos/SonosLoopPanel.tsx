import { useEffect, useState } from 'react';

import type { SonosControls } from './controls';
import { loopProblem, parseLoopTime, type LoopDraft } from './loop';
import { formatTrackClock } from './progress';
import type { SonosLoop } from './types';
import './loop.css';

interface SonosLoopPanelProps {
  id: string;
  loop: SonosLoop;
  draft: LoopDraft;
  positionSeconds: number | null;
  durationSeconds: number | null;
  controls: SonosControls;
}

interface LoopEndpointProps {
  label: string;
  value: number | null;
  positionSeconds: number | null;
  durationSeconds: number | null;
  disabled: boolean;
  onChange: (seconds: number) => void;
}

function clockText(seconds: number | null): string {
  return seconds === null ? '' : formatTrackClock(seconds);
}

function LoopEndpoint({
  label,
  value,
  positionSeconds,
  durationSeconds,
  disabled,
  onChange,
}: LoopEndpointProps) {
  const [text, setText] = useState(clockText(value));

  useEffect(() => {
    setText(clockText(value));
  }, [value]);

  const limit = durationSeconds ?? Number.POSITIVE_INFINITY;
  const commit = () => {
    const parsed = parseLoopTime(text);
    if (parsed === null || parsed > limit) {
      setText(clockText(value));
      return;
    }
    setText(clockText(parsed));
    onChange(parsed);
  };
  const nudge = (delta: number) => {
    const base = value ?? Math.floor(positionSeconds ?? 0);
    onChange(Math.max(0, Math.min(limit, base + delta)));
  };

  return (
    <div className="sonos-loop__endpoint" role="group" aria-label={`Loop ${label}`}>
      <label>
        <span>{label}</span>
        <input
          type="text"
          value={text}
          placeholder="m:ss"
          autoComplete="off"
          disabled={disabled}
          onChange={(event) => setText(event.currentTarget.value)}
          onBlur={commit}
          onKeyDown={(event) => {
            if (event.key === 'Enter') commit();
          }}
        />
      </label>
      <button
        type="button"
        disabled={disabled}
        aria-label={`${label}: 1 second earlier`}
        onClick={() => nudge(-1)}
      >
        −1
      </button>
      <button
        type="button"
        disabled={disabled}
        aria-label={`${label}: 1 second later`}
        onClick={() => nudge(1)}
      >
        +1
      </button>
      <button
        type="button"
        disabled={disabled || positionSeconds === null}
        aria-label={`Set ${label} to the current position`}
        onClick={() => onChange(Math.floor(positionSeconds ?? 0))}
      >
        Now
      </button>
    </div>
  );
}

/** A-B repeat controls; the Pi runs the loop so it survives a locked phone. */
export function SonosLoopPanel({
  id,
  loop,
  draft,
  positionSeconds,
  durationSeconds,
  controls,
}: SonosLoopPanelProps) {
  const problem = loopProblem(draft.start, draft.end, durationSeconds);
  const changed =
    !loop.active || draft.start !== loop.startSeconds || draft.end !== loop.endSeconds;
  const endpoint = { positionSeconds, durationSeconds, disabled: controls.running };

  return (
    <section id={id} className="sonos-loop" aria-label="Loop">
      <LoopEndpoint label="From" value={draft.start} onChange={draft.setStart} {...endpoint} />
      <LoopEndpoint label="To" value={draft.end} onChange={draft.setEnd} {...endpoint} />
      {draft.start !== null && draft.end !== null && problem && (
        <p className="sonos-loop__message">{problem}</p>
      )}
      {!loop.active && loop.notice && <p className="sonos-loop__message">{loop.notice}</p>}
      <div className="sonos-loop__actions">
        <button
          className="primary-button"
          type="button"
          disabled={controls.running || problem !== null || !changed}
          onClick={() => {
            if (draft.start !== null && draft.end !== null) {
              void controls.startLoop(draft.start, draft.end);
            }
          }}
        >
          {loop.active ? 'Update loop' : 'Start loop'}
        </button>
        {loop.active && (
          <button
            className="secondary-button"
            type="button"
            disabled={controls.running}
            onClick={() => void controls.stopLoop()}
          >
            Stop loop
          </button>
        )}
      </div>
    </section>
  );
}
