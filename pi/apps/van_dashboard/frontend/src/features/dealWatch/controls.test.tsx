import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ToastProvider } from '../../components/ToastProvider';
import type { PollingState } from '../../hooks/usePollingResource';
import { addDealWatchListing } from './api';
import { executeDealWatchMutation, useDealWatchControls } from './controls';
import { decodeDealWatchStatus } from './decoders';
import { dealWatchPayload } from './testFixtures';
import type { DealWatchMutationResult, DealWatchStatus } from './types';

vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api')>()),
  addDealWatchListing: vi.fn(),
}));

const addListingMock = vi.mocked(addDealWatchListing);
const draft = {
  parser: 'amazon' as const,
  threshold: '55.00',
  url: 'https://example.com/item',
  title: 'Example',
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((complete) => {
    resolve = complete;
  });
  return { promise, resolve };
}

function resource(): PollingState<DealWatchStatus> {
  const data = decodeDealWatchStatus(dealWatchPayload());
  return {
    data,
    error: null,
    initialLoading: false,
    refreshing: false,
    lastUpdatedAt: Date.now(),
    refresh: vi.fn().mockResolvedValue(data),
  };
}

describe('Deal Watch controls', () => {
  beforeEach(() => addListingMock.mockReset());
  afterEach(cleanup);

  it('allows only one mutation flight and reconciles after it settles', async () => {
    const pending = deferred<DealWatchMutationResult>();
    addListingMock.mockReturnValue(pending.promise);
    const dealResource = resource();
    const { result } = renderHook(() => useDealWatchControls(dealResource), {
      wrapper: ToastProvider,
    });

    let first!: Promise<boolean>;
    let duplicate!: Promise<boolean>;
    act(() => {
      first = result.current.addListing(draft);
      duplicate = result.current.addListing(draft);
    });
    expect(addListingMock).toHaveBeenCalledOnce();

    await act(async () => {
      pending.resolve({ message: 'Watching Example' });
      await first;
    });

    await expect(duplicate).resolves.toBe(false);
    expect(dealResource.refresh).toHaveBeenCalledOnce();
  });

  it('does not retry a failed mutation and still performs an authoritative refresh', async () => {
    const mutation = vi.fn().mockRejectedValue(new Error('response lost'));
    const refresh = vi.fn().mockResolvedValue(null);
    const showToast = vi.fn();

    const completed = await executeDealWatchMutation(mutation, refresh, showToast);

    expect(completed).toBe(false);
    expect(mutation).toHaveBeenCalledOnce();
    expect(showToast).toHaveBeenCalledWith('response lost', 'error');
    expect(refresh).toHaveBeenCalledOnce();
  });
});
