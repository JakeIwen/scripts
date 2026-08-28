import { useEffect, useMemo, useState } from 'react';

import type { SonosNowPlaying, SonosTrackProgress } from './types';

export function formatTrackClock(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const hours = Math.floor(seconds / 3_600);
  const minutes = Math.floor((seconds % 3_600) / 60);
  const remainder = seconds % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`;
  }
  return `${minutes}:${String(remainder).padStart(2, '0')}`;
}

export function projectTrackProgress(
  track: SonosNowPlaying | null,
  sampledAt: number | null,
  now = Date.now(),
): SonosTrackProgress | null {
  if (!track?.durationSeconds || track.durationSeconds <= 0) return null;

  const sampledPosition = track.positionSeconds ?? 0;
  const elapsed =
    track.transportState === 'PLAYING' && sampledAt !== null
      ? Math.max(0, (now - sampledAt) / 1_000)
      : 0;
  const positionSeconds = Math.min(track.durationSeconds, sampledPosition + elapsed);
  const percent = (positionSeconds / track.durationSeconds) * 100;

  return {
    positionSeconds,
    durationSeconds: track.durationSeconds,
    percent,
    label: `${formatTrackClock(positionSeconds)} of ${formatTrackClock(track.durationSeconds)}`,
  };
}

/**
 * Advance track position locally between ten-second Sonos snapshots. The timer
 * stops while the page is hidden and never makes a network request.
 */
export function useSonosTrackProgress(
  track: SonosNowPlaying | null,
  sampledAt: number | null,
): SonosTrackProgress | null {
  const [now, setNow] = useState(() => Date.now());
  const playing = track?.transportState === 'PLAYING' && Boolean(track.durationSeconds);

  useEffect(() => {
    let interval: number | null = null;
    const update = () => setNow(Date.now());
    const stop = () => {
      if (interval !== null) window.clearInterval(interval);
      interval = null;
    };
    const start = () => {
      stop();
      update();
      if (playing && !document.hidden) {
        interval = window.setInterval(update, 1_000);
      }
    };
    const visibilityChanged = () => {
      if (document.hidden) stop();
      else start();
    };

    start();
    document.addEventListener('visibilitychange', visibilityChanged);
    window.addEventListener('focus', start);
    window.addEventListener('pageshow', start);
    return () => {
      stop();
      document.removeEventListener('visibilitychange', visibilityChanged);
      window.removeEventListener('focus', start);
      window.removeEventListener('pageshow', start);
    };
  }, [playing, sampledAt, track?.durationSeconds, track?.positionSeconds]);

  return useMemo(() => projectTrackProgress(track, sampledAt, now), [now, sampledAt, track]);
}
