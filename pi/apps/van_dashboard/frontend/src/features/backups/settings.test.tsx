import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { BackupSettingsPanel } from './BackupSettingsPanel';
import { backupPreferencesPayload } from './testFixtures';

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute('open', '');
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute('open');
  };
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('backup settings', () => {
  it('keeps edits across categories and saves only changed values with their revision', async () => {
    const fixture = backupPreferencesPayload();
    const fetch = vi.fn(async (_path: unknown, init?: RequestInit) => {
      if (init?.method === 'POST') {
        const request = JSON.parse(String(init.body));
        Object.assign(fixture.settings.values, request.changes);
        fixture.settings.revision = 'revision-two';
      }
      return new Response(JSON.stringify(fixture));
    });
    vi.stubGlobal('fetch', fetch);
    const refresh = vi.fn().mockResolvedValue(null);
    render(<BackupSettingsPanel refresh={refresh} capacityControl={null} />);
    expect(fetch).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Backup settings' }));
    fireEvent.change(await screen.findByLabelText('Every'), { target: { value: '3' } });
    fireEvent.click(screen.getByRole('button', { name: 'Mac · iCloud' }));
    fireEvent.change(screen.getByLabelText('Keep'), { target: { value: '4' } });
    expect(fetch.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
    const post = fetch.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(post?.[0]).toBe('/api/backups/settings');
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({
      revision: 'revision-one',
      changes: { 'pi.interval': 3, 'mac.keep': 4 },
    });
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled();
  });

  it('puts explanation in a tooltip and protects unsaved changes on close', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify(backupPreferencesPayload()))),
    );
    render(<BackupSettingsPanel refresh={vi.fn()} capacityControl={null} />);
    fireEvent.click(screen.getByRole('button', { name: 'Backup settings' }));
    const input = await screen.findByLabelText('Every');
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
    fireEvent.focus(screen.getByRole('button', { name: 'About Every' }));
    expect(screen.getByRole('tooltip')).toHaveTextContent(
      'Time between successful cloud verifications.',
    );
    fireEvent.keyDown(screen.getByRole('button', { name: 'About Every' }), { key: 'Escape' });
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
    fireEvent.change(input, { target: { value: '4' } });
    fireEvent.click(screen.getByRole('button', { name: 'Close Backup settings' }));
    expect(screen.getByRole('dialog', { name: 'Backup settings' })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Keep editing' }));
    expect(input).toHaveValue(4);
    fireEvent.click(screen.getByRole('button', { name: 'Cancel changes' }));
    expect(input).toHaveValue(7);
  });

  it('retains a draft after a conflict and provides an explicit reload', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async (_path, init) =>
          new Response(
            JSON.stringify(
              init?.method === 'POST'
                ? { ok: false, message: 'Settings changed elsewhere. Reload before saving.' }
                : backupPreferencesPayload(),
            ),
            { status: init?.method === 'POST' ? 409 : 200 },
          ),
      ),
    );
    render(<BackupSettingsPanel refresh={vi.fn()} capacityControl={null} />);
    fireEvent.click(screen.getByRole('button', { name: 'Backup settings' }));
    fireEvent.change(await screen.findByLabelText('Every'), { target: { value: '4' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('changed elsewhere');
    expect(screen.getByLabelText('Every')).toHaveValue(4);
    fireEvent.click(screen.getByRole('button', { name: 'Reload & discard edits' }));
    await waitFor(() => expect(screen.getByLabelText('Every')).toHaveValue(7));
  });
});
