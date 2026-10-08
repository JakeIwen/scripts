import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { BottomSheet } from '../../components/BottomSheet';
import { ICloudAttemptMessage } from './ICloudAttemptMessage';

const id = 'a'.repeat(32);
const message = 'Attempt failed; see the private service log for details.';
const response = {
  ok: true,
  details: {
    attempt: {
      id,
      started_at: 1800000000,
      ended_at: 1800000100,
      phase: 'error',
      work_phase: 'preparing',
      generation: 'tm-20260930T010203Z-12345678',
      message,
      verified_at: null,
      progress: {
        upload_estimated_bytes: null,
        upload_total_bytes: null,
        command_bytes: null,
        verified_bytes: null,
        verification_total_bytes: null,
        verified_files: null,
        verification_total_files: null,
        current_file_bytes: null,
      },
    },
    entries: [
      {
        at: 1800000099,
        message: 'Failed: unsafe staging object; cleanup refused',
        explanation: 'Cleanup stopped to protect backup data.',
      },
    ],
    note: null as string | null,
  },
};

beforeAll(() => {
  if (!HTMLDialogElement.prototype.showModal) {
    HTMLDialogElement.prototype.showModal = function () {
      this.setAttribute('open', '');
    };
  }
  if (!HTMLDialogElement.prototype.close) {
    HTMLDialogElement.prototype.close = function () {
      this.removeAttribute('open');
      this.dispatchEvent(new Event('close'));
    };
  }
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('cloud failure details', () => {
  it.each(['pi', 'time-machine'] as const)(
    'loads the exact %s attempt only when details is opened',
    async (kind) => {
      const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => response });
      vi.stubGlobal('fetch', fetch);
      render(<ICloudAttemptMessage kind={kind} id={id} phase="error" message={message} />);
      expect(fetch).not.toHaveBeenCalled();
      expect(screen.queryByText(/private service log/)).not.toBeInTheDocument();
      expect(screen.getByText(/Attempt failed;/)).toHaveTextContent('Attempt failed; details.');
      fireEvent.click(screen.getByRole('button', { name: 'details' }));
      expect(
        await screen.findByText('Failed: unsafe staging object; cleanup refused'),
      ).toBeVisible();
      expect(fetch).toHaveBeenCalledWith(
        `/api/backups/cloud/${kind}/attempts/${id}`,
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      );
      const dialog = screen.getByRole('dialog', { name: 'Backup attempt details' });
      expect(within(dialog).getByText('Stage reached')).toBeVisible();
      expect(within(dialog).getByText('Preparing')).toBeVisible();
      fireEvent.click(within(dialog).getByRole('button', { name: 'Close Backup attempt details' }));
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    },
  );

  it('shows missing journal evidence explicitly', async () => {
    const note =
      'No detailed failure reason is available in the retained service journal for this attempt.';
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          ...response,
          details: { ...response.details, entries: [], note },
        }),
      }),
    );
    render(<ICloudAttemptMessage kind="pi" id={id} phase="error" message={message} />);
    fireEvent.click(screen.getByRole('button', { name: 'details' }));
    expect(await screen.findByText(note)).toBeVisible();
  });

  it('can retry loading after an API error without resuming the backup', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce({
        ok: false,
        status: 503,
        json: async () => ({ ok: false, message: 'Details unavailable.' }),
      })
      .mockResolvedValueOnce({ ok: true, json: async () => response });
    vi.stubGlobal('fetch', fetch);
    render(<ICloudAttemptMessage kind="pi" id={id} phase="error" message={message} />);
    fireEvent.click(screen.getByRole('button', { name: 'details' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Details unavailable.');
    fireEvent.click(screen.getByRole('button', { name: 'Retry loading details' }));
    expect(await screen.findByText('Cleanup stopped to protect backup data.')).toBeVisible();
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(fetch.mock.calls.every(([path]) => path.endsWith(`/attempts/${id}`))).toBe(true);
  });

  it('Escape closes only the details popup and preserves its parent Backups sheet', async () => {
    const closeParent = vi.fn();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => response }));
    render(
      <BottomSheet open title="Backups" onClose={closeParent}>
        <ICloudAttemptMessage kind="time-machine" id={id} phase="error" message={message} />
      </BottomSheet>,
    );
    const opener = screen.getByRole('button', { name: 'details' });
    opener.focus();
    fireEvent.click(opener);
    await screen.findByText('Cleanup stopped to protect backup data.');
    fireEvent(
      screen.getByRole('dialog', { name: 'Backup attempt details' }),
      new Event('cancel', { bubbles: true, cancelable: true }),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole('dialog', { name: 'Backup attempt details' }),
      ).not.toBeInTheDocument(),
    );
    expect(closeParent).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog', { name: 'Backups' })).toHaveAttribute('open');
    expect(opener).toHaveFocus();
  });
});
