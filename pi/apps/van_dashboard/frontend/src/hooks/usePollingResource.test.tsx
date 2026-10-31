import { StrictMode } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { usePollingResource } from './usePollingResource';

type Refresh = (options?: { fresh?: boolean }) => Promise<string | null>;

function PollingProbe({
  load,
  onRefresh,
}: {
  load: (signal: AbortSignal) => Promise<string>;
  onRefresh?: (refresh: Refresh) => void;
}) {
  const resource = usePollingResource({ load, intervalMs: 60_000 });
  onRefresh?.(resource.refresh);
  return <output>{resource.data ?? 'loading'}</output>;
}

describe('usePollingResource', () => {
  it('starts a fresh request after the StrictMode cleanup aborts the first mount', async () => {
    let calls = 0;
    const load = vi.fn((signal: AbortSignal) => {
      calls += 1;
      return new Promise<string>((resolve, reject) => {
        queueMicrotask(() => {
          if (signal.aborted) reject(new DOMException('aborted', 'AbortError'));
          else resolve(`ready-${calls}`);
        });
      });
    });

    render(
      <StrictMode>
        <PollingProbe load={load} />
      </StrictMode>,
    );

    await waitFor(() => expect(screen.getByText(/^ready-/)).toBeInTheDocument());
    expect(load.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it('runs a queued fresh read with the current loader after the loader changes', async () => {
    const loadA = vi.fn(
      (signal: AbortSignal) =>
        new Promise<string>((_, reject) => {
          signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
        }),
    );
    const loadB = vi.fn(async () => 'B-current');
    let refresh: Refresh | null = null;
    const keep = (next: Refresh) => {
      refresh = next;
    };
    const { rerender } = render(<PollingProbe load={loadA} onRefresh={keep} />);
    await waitFor(() => expect(loadA).toHaveBeenCalledTimes(1));
    const queued = refresh!({ fresh: true });
    rerender(<PollingProbe load={loadB} onRefresh={keep} />);
    await waitFor(() => expect(screen.getByText('B-current')).toBeInTheDocument());
    await queued;
    expect(loadA).toHaveBeenCalledTimes(1);
    expect(screen.getByText('B-current')).toBeInTheDocument();
  });
});
