import { act, cleanup, render } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { OPENWRT_CLIENT_INTERVAL_MS } from '../hooks';
import { MeteredSection } from './MeteredSection';
import { meteredFixture } from './testFixtures';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it('only polls while the sheet is open, at the existing sheet cadence', async () => {
  vi.useFakeTimers();
  const fetch = vi.fn(async () => new Response(JSON.stringify(meteredFixture())));
  vi.stubGlobal('fetch', fetch);
  const view = render(<MeteredSection open={false} />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(OPENWRT_CLIENT_INTERVAL_MS);
  });
  expect(fetch).not.toHaveBeenCalled();
  view.rerender(<MeteredSection open />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(fetch).toHaveBeenCalledTimes(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(OPENWRT_CLIENT_INTERVAL_MS - 1);
  });
  expect(fetch).toHaveBeenCalledTimes(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(fetch).toHaveBeenCalledTimes(2);
  view.rerender(<MeteredSection open={false} />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(OPENWRT_CLIENT_INTERVAL_MS);
  });
  expect(fetch).toHaveBeenCalledTimes(2);
});
