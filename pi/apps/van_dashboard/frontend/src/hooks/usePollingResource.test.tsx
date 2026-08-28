import { StrictMode } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { usePollingResource } from './usePollingResource';

function PollingProbe({ load }: { load: (signal: AbortSignal) => Promise<string> }) {
  const resource = usePollingResource({ load, intervalMs: 60_000 });
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
});
