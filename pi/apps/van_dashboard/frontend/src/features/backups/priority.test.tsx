import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { BackupPriorityMenu } from './BackupPriorityMenu';
import { sampleBackupStatus } from './testFixtures';

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
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('backup priority menu', () => {
  it('loads in its own dialog and applies a one-time Mac priority', async () => {
    const status = fixture();
    const fetch = vi.fn(async (_url: unknown, init?: RequestInit) => {
      if (init?.method === 'POST')
        status.mode = new URLSearchParams(String(init.body)).get('mode')!;
      return new Response(JSON.stringify({ ok: true, priority: status }), { status: 200 });
    });
    vi.stubGlobal('fetch', fetch);
    render(
      <BackupPriorityMenu
        backups={sampleBackupStatus()}
        refresh={vi.fn().mockResolvedValue(null)}
      />,
    );
    expect(fetch).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Backup priority' }));
    const dialog = screen.getByRole('dialog', { name: 'Backup priority' });
    const selection = await within(dialog).findByRole('combobox', { name: 'Priority' });
    fireEvent.change(selection, { target: { value: 'time-machine' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Apply priority' }));
    expect(
      await within(dialog).findByText(
        'Mac iCloud has priority until this recovery point is verified.',
      ),
    ).toBeVisible();
    expect(
      fetch.mock.calls.some(
        ([url, init]) =>
          url === '/api/backups/priority' && String(init?.body) === 'mode=time-machine',
      ),
    ).toBe(true);
    expect(within(dialog).getByText('Disk backup freshness')).toBeVisible();
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
    render(<BackupPriorityMenu backups={null} refresh={vi.fn().mockResolvedValue(null)} />);
    fireEvent.click(screen.getByRole('button', { name: 'Backup priority' }));
    const button = await screen.findByRole('button', {
      name: 'Stop current Mac backup for capture',
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
      render(<BackupPriorityMenu backups={null} refresh={vi.fn()} />);
      fireEvent.click(screen.getByRole('button', { name: 'Backup priority' }));
      expect(
        await screen.findByRole('button', { name: 'Stop current Mac backup for capture' }),
      ).toBeDisabled();
    },
  );
});
