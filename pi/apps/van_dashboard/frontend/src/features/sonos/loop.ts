import { useEffect, useState } from 'react';

import type { SonosLoop } from './types';

// Mirrors sonos_loop.MIN_LOOP_SECONDS; the Pi rejects anything shorter.
export const MIN_LOOP_SECONDS = 2;

/** Parse a typed loop time: seconds ("83"), M:SS ("1:23") or H:MM:SS. */
export function parseLoopTime(text: string): number | null {
  const trimmed = text.trim();
  if (!/^\d+(:\d{1,2}){0,2}$/.test(trimmed)) return null;
  const parts = trimmed.split(':').map(Number);
  if (parts.slice(1).some((part) => part > 59)) return null;
  return parts.reduce((total, part) => total * 60 + part, 0);
}

/** Why the drafted range can't be looped, or null when it can. */
export function loopProblem(
  start: number | null,
  end: number | null,
  durationSeconds: number | null,
): string | null {
  if (start === null || end === null) return 'Set both ends of the loop';
  if (durationSeconds !== null && end > durationSeconds) return 'The loop ends after the track';
  if (end - start < MIN_LOOP_SECONDS) {
    return `End the loop at least ${MIN_LOOP_SECONDS} seconds after it starts`;
  }
  return null;
}

export interface LoopDraft {
  start: number | null;
  end: number | null;
  setStart: (seconds: number) => void;
  setEnd: (seconds: number) => void;
}

/** Editable loop ends, reset to the Pi's loop whenever that loop changes. */
export function useLoopDraft(loop: SonosLoop): LoopDraft {
  const activeStart = loop.active ? loop.startSeconds : null;
  const activeEnd = loop.active ? loop.endSeconds : null;
  const [start, setStart] = useState(activeStart);
  const [end, setEnd] = useState(activeEnd);

  useEffect(() => {
    if (activeStart === null || activeEnd === null) return;
    setStart(activeStart);
    setEnd(activeEnd);
  }, [activeStart, activeEnd]);

  return { start, end, setStart, setEnd };
}

export interface LoopRegion {
  start: number;
  end: number;
  draft: boolean;
}

/** The range to mark on the progress bar: the running loop, else a valid draft. */
export function loopRegion(
  loop: SonosLoop,
  draft: LoopDraft,
  editing: boolean,
  durationSeconds: number | null,
): LoopRegion | null {
  if (!durationSeconds) return null;
  if (loop.active) return { start: loop.startSeconds, end: loop.endSeconds, draft: false };
  if (!editing || draft.start === null || draft.end === null) return null;
  if (loopProblem(draft.start, draft.end, durationSeconds)) return null;
  return { start: draft.start, end: draft.end, draft: true };
}
