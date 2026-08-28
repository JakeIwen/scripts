import { useCallback, useRef, useState } from 'react';

export function useSingleFlightAction() {
  const [running, setRunning] = useState(false);
  const runningRef = useRef(false);

  const run = useCallback(async <T>(action: () => Promise<T>): Promise<T | null> => {
    if (runningRef.current) return null;
    runningRef.current = true;
    setRunning(true);
    try {
      return await action();
    } finally {
      runningRef.current = false;
      setRunning(false);
    }
  }, []);

  return { running, run };
}
