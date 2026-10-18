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
import { BackupSettingsPanel } from './BackupSettingsPanel';
import { backupPreferencesPayload } from './testFixtures';
import { useBackupPreferences } from './useBackupPreferences';
import { decodeBackupPreferences } from './settings';
import { prepareSettingsDraft } from './settingsDraft';

function render(ui: ReactNode) {
  return testingRender(ui, { wrapper: ToastProvider });
}

function BurstSaveProbe() {
  const editor = useBackupPreferences(true, async () => null);
  return editor.settings ? (
    <button
      onClick={() => {
        void editor.commit('pi.interval', 3);
        void editor.commit('pi.interval', 3);
      }}
    >
      Submit twice
    </button>
  ) : (
    <span>Loading</span>
  );
}

function mockSettings() {
  const fixture = backupPreferencesPayload();
  const fetch = vi.fn(async (_path: unknown, init?: RequestInit) => {
    if (init?.method === 'POST') {
      const request = JSON.parse(String(init.body));
      Object.assign(fixture.settings.values, request.changes);
      fixture.settings.revision = `${fixture.settings.revision}-next`;
    }
    return new Response(JSON.stringify(fixture));
  });
  vi.stubGlobal('fetch', fetch);
  return fetch;
}

async function openPanel(refresh = vi.fn().mockResolvedValue(null)) {
  render(<BackupSettingsPanel refresh={refresh} capacityControl={null} />);
  fireEvent.click(screen.getByRole('button', { name: 'Backup settings' }));
  return await screen.findByLabelText('Every');
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
    fireEvent.click(await screen.findByRole('button', { name: 'Submit twice' }));
    await act(async () => {
      completions.forEach((finish) => finish());
    });
    expect(fetch.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
  });

  it('saves completed numbers on blur and uses the new revision for the next edit', async () => {
    const fetch = mockSettings();
    const input = await openPanel();
    expect(screen.queryByRole('button', { name: 'Save changes' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Cancel changes' })).not.toBeInTheDocument();
    fireEvent.change(input, { target: { value: '3' } });
    expect(fetch.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0);
    fireEvent.blur(input);
    expect(await screen.findByText('Pi · iCloud · Every updated')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Mac · iCloud' }));
    const keep = screen.getByLabelText('Keep');
    fireEvent.change(keep, { target: { value: '4' } });
    fireEvent.blur(keep);
    expect(await screen.findByText('Mac · iCloud · Keep updated')).toBeVisible();
    const posts = fetch.mock.calls.filter(([, init]) => init?.method === 'POST');
    expect(posts.map(([, init]) => JSON.parse(String(init?.body)))).toEqual([
      { revision: 'revision-one', changes: { 'pi.interval': 3 } },
      { revision: 'revision-one-next', changes: { 'mac.keep': 4 } },
    ]);
  });

  it('saves a focused number on Enter without a second write on blur', async () => {
    const fetch = mockSettings();
    const input = await openPanel();
    act(() => input.focus());
    fireEvent.change(input, { target: { value: '4' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(await screen.findByText('Pi · iCloud · Every updated')).toBeVisible();
    fireEvent.blur(input);
    expect(fetch.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
  });

  it('rejects an invalid number without preventing changes in another category', async () => {
    const fetch = mockSettings();
    const input = await openPanel();
    fireEvent.change(input, { target: { value: '32' } });
    fireEvent.blur(input);
    expect(screen.getByText(/Not saved.*1–31/)).toBeVisible();
    expect(fetch.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: 'Alerts' }));
    fireEvent.click(screen.getByRole('switch', { name: 'Successful disk backups' }));
    expect(await screen.findByText('Alerts · Successful disk backups updated')).toBeVisible();
    expect(screen.getByRole('switch')).toHaveAttribute('aria-checked', 'false');
    fireEvent.click(screen.getByRole('button', { name: /Pi · iCloud/ }));
    expect(screen.getByLabelText('Every')).toHaveValue(32);
    expect(screen.getByLabelText('Every')).toHaveAttribute('aria-invalid', 'true');
    const posts = fetch.mock.calls.filter(([, init]) => init?.method === 'POST');
    expect(posts.map(([, init]) => JSON.parse(String(init?.body)).changes)).toEqual([
      { 'alerts.success': false },
    ]);
  });

  it('finishes a pending change and keeps its lock when the dialog closes and reopens', async () => {
    const fixture = backupPreferencesPayload();
    let finish: (() => void) | undefined;
    const fetch = vi.fn(async (_path: unknown, init?: RequestInit) =>
      init?.method === 'POST'
        ? new Promise<Response>((resolve) => {
            finish = () => {
              fixture.settings.values['pi.interval'] = 4;
              resolve(new Response(JSON.stringify(fixture)));
            };
          })
        : new Response(JSON.stringify(fixture)),
    );
    vi.stubGlobal('fetch', fetch);
    const input = await openPanel();
    fireEvent.change(input, { target: { value: '4' } });
    fireEvent.blur(input);
    fireEvent.click(screen.getByRole('button', { name: 'Close Backup settings' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Backup settings' }));
    expect(screen.getByLabelText('Every')).toBeDisabled();
    expect(screen.queryByText('Pi · iCloud · Every updated')).not.toBeInTheDocument();
    await act(async () => finish?.());
    expect(await screen.findByText('Pi · iCloud · Every updated')).toBeVisible();
    expect(screen.getByLabelText('Every')).toHaveValue(4);
    expect(screen.getByLabelText('Every')).toBeEnabled();
    expect(fetch.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
    expect(fetch.mock.calls.filter(([, init]) => init?.method !== 'POST')).toHaveLength(1);
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
    fireEvent.blur(screen.getByLabelText('Every'));
    expect(await screen.findByRole('alert')).toHaveTextContent('changed elsewhere');
    expect(screen.getByLabelText('Every')).toHaveValue(4);
    fireEvent.click(screen.getByRole('button', { name: 'Reload & discard edits' }));
    await waitFor(() => expect(screen.getByLabelText('Every')).toHaveValue(7));
  });
});
