import { usePollingResource, type PollingState } from '../../hooks/usePollingResource';
import { fetchSonosStatus } from './api';
import type { SonosStatus } from './types';

// Sonos discovery is cached by the backend. This interval refreshes current
// playback without creating overlapping requests or polling while hidden.
export const SONOS_POLL_INTERVAL_MS = 10_000;

export function useSonosStatus(): PollingState<SonosStatus> {
  return usePollingResource({
    load: fetchSonosStatus,
    intervalMs: SONOS_POLL_INTERVAL_MS,
  });
}
