import { useEffect, useState } from 'react';

export function ignitionRemainingSeconds(
  deadline: number | null,
  nowMilliseconds = Date.now(),
): number | null {
  if (deadline === null) return null;
  return Math.max(0, Math.ceil(deadline - nowMilliseconds / 1_000));
}

export function formatIgnitionDuration(totalSeconds: number | null): string {
  if (totalSeconds === null) return 'not paused';
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const days = Math.floor(seconds / 86_400);
  const hours = Math.floor((seconds % 86_400) / 3_600);
  const minutes = Math.floor((seconds % 3_600) / 60);
  const remainder = seconds % 60;
  const parts: string[] = [];
  if (days > 0) parts.push(`${days}d`);
  if (days > 0 || hours > 0) parts.push(`${hours}h`);
  if (days === 0) parts.push(`${minutes}m`);
  if (days === 0 && hours === 0) parts.push(`${remainder}s`);
  return parts.join(' ');
}

/** Update the visible pause countdown locally; API polling remains bounded. */
export function useIgnitionCountdown(deadline: number | null): number | null {
  const [remaining, setRemaining] = useState(() => ignitionRemainingSeconds(deadline));

  useEffect(() => {
    let interval: number | null = null;
    const update = () => setRemaining(ignitionRemainingSeconds(deadline));
    const stop = () => {
      if (interval !== null) window.clearInterval(interval);
      interval = null;
    };
    const start = () => {
      stop();
      update();
      if (deadline !== null && !document.hidden) {
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
  }, [deadline]);

  return remaining;
}
