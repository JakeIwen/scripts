import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { BackupSettingsPanel } from './BackupSettingsPanel';
import { backupPreferencesPayload } from './testFixtures';
import { useBackupPreferences } from './useBackupPreferences';
import { decodeBackupPreferences } from './settings';
import { prepareSettingsDraft } from './settingsDraft';

function BurstSaveProbe() {
  const editor = useBackupPreferences(
    async () => null,
    () => {},
  );
  return editor.settings ? (
    <>
      <button onClick={() => editor.change('pi.interval', 3)}>Edit</button>
      <button
        onClick={(event) => {
          void editor.save(event);
          void editor.save(event);
        }}
      >
        Submit twice
      </button>
    </>
  ) : (
    <span>Loading</span>
  );
}

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
  it.each(['duplicate-field', 'duplicate-group', 'unknown-group', 'bad-bounds'] as const)(
    'rejects incoherent server metadata: %s',
    (kind) => {
      const payload = backupPreferencesPayload();
      const field = payload.settings.fields[0],
        group = payload.settings.groups[0];
      if (!field || !group) throw new Error('Settings fixture is incomplete');
      if (kind === 'duplicate-field') payload.settings.fields.push({ ...field });
      if (kind === 'duplicate-group') payload.settings.groups.push({ ...group });
      if (kind === 'unknown-group') field.group = 'missing';
      if (kind === 'bad-bounds') field.minimum = 50;
      expect(() => decodeBackupPreferences(payload)).toThrow(TypeError);
    },
  );

  it('prepares fractional transfer rates without a lossy payload cast', () => {
    const payload = backupPreferencesPayload();
    const field = payload.settings.fields[0];
    if (!field) throw new Error('Settings fixture is incomplete');
    Object.assign(field, { kind: 'bandwidth', unit: 'MiB/s', minimum: 0.125 });
    const settings = decodeBackupPreferences(payload);
    const valid = prepareSettingsDraft(settings, { ...settings.values, 'pi.interval': 0.9765625 });
    expect(valid.errors).toEqual({});
    expect(valid.changes).toEqual({ 'pi.interval': 0.9765625 });
    const invalid = prepareSettingsDraft(settings, { ...settings.values, 'pi.interval': 0.2 });
    expect(invalid.errors['pi.interval']).toContain('whole KiB/s');
    expect(invalid.changes).toEqual({});
  });
  it('admits only one save before React has rendered the busy state', async () => {
    const completions: (() => void)[] = [];
    const fetch = vi.fn(async (_path: unknown, init?: RequestInit) =>
      init?.method === 'POST'
        ? new Promise<Response>((resolve) =>
            completions.push(() =>
              resolve(new Response(JSON.stringify(backupPreferencesPayload()))),
            ),
          )
        : new Response(JSON.stringify(backupPreferencesPayload())),
    );
    vi.stubGlobal('fetch', fetch);
    render(<BurstSaveProbe />);
    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }));
    fireEvent.click(screen.getByRole('button', { name: 'Submit twice' }));
    await act(async () => {
      completions.forEach((finish) => finish());
    });
    expect(fetch.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
  });

  it('keeps Save disabled for an invalid value after changing categories', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify(backupPreferencesPayload()))),
    );
    render(<BackupSettingsPanel refresh={vi.fn()} capacityControl={null} />);
    fireEvent.click(screen.getByRole('button', { name: 'Backup settings' }));
    fireEvent.change(await screen.findByLabelText('Every'), { target: { value: '32' } });
    fireEvent.click(screen.getByRole('button', { name: 'Mac · iCloud' }));
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled();
  });
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
