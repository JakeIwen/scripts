import { ToastProvider } from '../../components/ToastProvider';
import type { ReactNode } from 'react';
import {
  act,
  cleanup,
  fireEvent,
  render as testingRender,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { BackupPriorityControls } from './BackupPriorityControls';
import { useSingleFlightAction } from '../../hooks/useSingleFlightAction';

function render(ui: ReactNode) {
  return testingRender(ui, { wrapper: ToastProvider });
}

function PriorityProbe({ refresh }: { refresh: () => Promise<unknown> }) {
  const action = useSingleFlightAction();
  return <BackupPriorityControls busy={action.running} runAction={action.run} refresh={refresh} />;
}

function fixture() {
  return {
    mode: 'normal',
    requested_at: null,
    completed: false,
    jobs: [
      {
        kind: 'pi',
        label: 'Pi iCloud',
        eligible: false,
        reason: 'This cloud backup is already current.',
        last_success_at: 1800000000,
        waiting: false,
      },
      {
        kind: 'time-machine',
        label: 'Mac iCloud',
        eligible: true,
        reason: null,
        last_success_at: null,
        waiting: false,
      },
    ],
    capture: { coordinator_ready: true, capture_complete: false, phase: 'none', expires_at: null },
  };
}
beforeAll(() => {
  if (!HTMLDialogElement.prototype.showModal)
    HTMLDialogElement.prototype.showModal = function () {
      this.setAttribute('open', '');
    };
  if (!HTMLDialogElement.prototype.close)
    HTMLDialogElement.prototype.close = function () {
      this.removeAttribute('open');
      this.dispatchEvent(new Event('close'));
    };
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('backup priority menu', () => {
  it('refreshes capture readiness while the category stays open', async () => {
    vi.useFakeTimers();
    const status = fixture();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ ok: true, priority: status }))),
    );
    await act(async () => {
      render(<PriorityProbe refresh={vi.fn().mockResolvedValue(null)} />);
    });
    const button = screen.getByRole('button', { name: 'Stop Mac backup for capture' });
    expect(button).toBeEnabled();
    status.capture.capture_complete = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(button).toBeDisabled();
  });
  it('does not label an unavailable source as an already current backup', async () => {
    const status = fixture();
    const pi = status.jobs[0];
    if (!pi) throw new Error('Priority fixture is incomplete');
    pi.reason = 'A fresh Pi Borg backup is needed.';
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ ok: true, priority: status }))),
    );
    render(<PriorityProbe refresh={vi.fn().mockResolvedValue(null)} />);
    const button = await screen.findByRole('button', { name: /Pi first/ });
    expect(button).toHaveTextContent('Unavailable');
    expect(button).toHaveAttribute('title', 'A fresh Pi Borg backup is needed.');
  });
  it('loads in its own dialog and applies a one-time Mac priority', async () => {
    const status = fixture();
    const fetch = vi.fn(async (_url: unknown, init?: RequestInit) => {
      if (init?.method === 'POST')
        status.mode = new URLSearchParams(String(init.body)).get('mode')!;
      return new Response(JSON.stringify({ ok: true, priority: status }), { status: 200 });
    });
    vi.stubGlobal('fetch', fetch);
    render(<PriorityProbe refresh={vi.fn().mockResolvedValue(null)} />);
    const button = await screen.findByRole('button', { name: /Mac first/ });
    fireEvent.click(button);
    await waitFor(() => expect(button).toHaveAttribute('aria-pressed', 'true'));
    expect(
      fetch.mock.calls.some(
        ([url, init]) =>
          url === '/api/backups/priority' && String(init?.body) === 'mode=time-machine',
      ),
    ).toBe(true);
  });

  it('requests a Mac stop only after an explicit button press', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ ok: true, priority: fixture() }), { status: 200 }),
      );
    fetch.mockImplementation(
      async () => new Response(JSON.stringify({ ok: true, priority: fixture() }), { status: 200 }),
    );
    vi.stubGlobal('fetch', fetch);
    render(<PriorityProbe refresh={vi.fn().mockResolvedValue(null)} />);
    const button = await screen.findByRole('button', {
      name: 'Stop Mac backup for capture',
    });
    expect(fetch.mock.calls.every(([, init]) => init?.method !== 'POST')).toBe(true);
    fireEvent.click(button);
    await waitFor(() =>
      expect(
        fetch.mock.calls.some(
          ([url, init]) => url === '/api/backups/priority/capture' && init?.method === 'POST',
        ),
      ).toBe(true),
    );
  });

  it.each(['coordinator', 'frozen'] as const)(
    'disables unnecessary or unavailable Mac stopping: %s',
    async (reason) => {
      const status = fixture();
      if (reason === 'coordinator') status.capture.coordinator_ready = false;
      else status.capture.capture_complete = true;
      vi.stubGlobal(
        'fetch',
        vi.fn(
          async () => new Response(JSON.stringify({ ok: true, priority: status }), { status: 200 }),
        ),
      );
      render(<PriorityProbe refresh={vi.fn().mockResolvedValue(null)} />);
      expect(
        await screen.findByRole('button', { name: 'Stop Mac backup for capture' }),
      ).toBeDisabled();
    },
  );
});
