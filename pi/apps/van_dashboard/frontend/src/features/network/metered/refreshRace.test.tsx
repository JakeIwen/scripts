import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { MeteredSection } from './MeteredSection';
import { meteredFixture } from './testFixtures';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('does not mistake an overlapping pre-write poll for the post-write refetch', async () => {
  const status = meteredFixture();
  let gets = 0;
  let posts = 0;
  let finishWrite!: (response: Response) => void;
  let finishOldRead!: (response: Response) => void;
  let oldSnapshot = '';
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_path: string, init?: RequestInit) => {
      if (init?.method === 'POST') {
        posts += 1;
        return new Promise<Response>((resolve) => {
          finishWrite = resolve;
        });
      }
      gets += 1;
      if (gets === 2) {
        oldSnapshot = JSON.stringify(status);
        return new Promise<Response>((resolve) => {
          finishOldRead = resolve;
        });
      }
      return new Response(JSON.stringify(status));
    }),
  );
  render(<MeteredSection open />);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Add SSID' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Remove SSID denlink' }));
  await waitFor(() => expect(posts).toBe(1));
  fireEvent(window, new Event('focus'));
  await waitFor(() => expect(gets).toBe(2));
  status.config.metered_ssids = [];
  status.config.revision += 1;
  status.state.config_revision = status.config.revision;
  await act(async () => {
    finishWrite(new Response(JSON.stringify({ ok: true, config: status.config })));
  });
  await act(async () => {
    finishOldRead(new Response(oldSnapshot));
  });
  await screen.findByText('No metered SSIDs');
  expect(gets).toBe(3);
  expect(posts).toBe(1);
  expect(screen.queryByRole('button', { name: 'Remove SSID denlink' })).not.toBeInTheDocument();
});
