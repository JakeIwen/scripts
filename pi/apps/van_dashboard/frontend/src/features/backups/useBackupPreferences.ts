import { useEffect, useState, type FormEvent } from 'react';
import {
  fetchBackupPreferences,
  saveBackupPreferences,
  type BackupPreferences,
  type SettingValue,
} from './settings';

interface PreferenceState {
  settings: BackupPreferences | null;
  draft: Record<string, SettingValue | ''>;
  busy: boolean;
  error: string | null;
  saved: boolean;
  discard: boolean;
}
const initial: PreferenceState = {
  settings: null,
  draft: {},
  busy: false,
  error: null,
  saved: false,
  discard: false,
};

export function useBackupPreferences(refresh: () => Promise<unknown>, onClose: () => void) {
  const [state, setState] = useState(initial);
  const update = (patch: Partial<PreferenceState>) =>
    setState((current) => ({ ...current, ...patch }));
  const accept = (settings: BackupPreferences) =>
    update({ settings, draft: settings.values, error: null });
  const changes = Object.fromEntries(
    Object.entries(state.draft).filter(([key, value]) => state.settings?.values[key] !== value),
  );
  const count = Object.keys(changes).length;
  const canSave = !!count && !state.busy && !Object.values(changes).includes('');
  useEffect(() => {
    const abort = new AbortController();
    fetchBackupPreferences(abort.signal)
      .then(accept)
      .catch((reason) => {
        if (!abort.signal.aborted)
          update({ error: reason instanceof Error ? reason.message : 'Settings unavailable.' });
      });
    return () => abort.abort();
  }, []);
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!state.settings || !canSave) return;
    update({ busy: true, error: null, saved: false });
    try {
      accept(
        await saveBackupPreferences(
          state.settings.revision,
          changes as Record<string, SettingValue>,
        ),
      );
      update({ saved: true });
      await refresh();
    } catch (reason) {
      update({ error: reason instanceof Error ? reason.message : 'Settings could not be saved.' });
    } finally {
      update({ busy: false });
    }
  }
  async function reload() {
    update({ busy: true, error: null });
    try {
      accept(await fetchBackupPreferences());
      update({ saved: false });
    } catch (reason) {
      update({ error: reason instanceof Error ? reason.message : 'Settings unavailable.' });
    } finally {
      update({ busy: false });
    }
  }
  function close() {
    if (state.busy) return;
    if (count) update({ discard: true });
    else onClose();
  }
  return {
    ...state,
    changes,
    count,
    canSave,
    save,
    reload,
    close,
    setBusy: (busy: boolean) => update({ busy }),
    keepEditing: () => update({ discard: false }),
    change: (key: string, value: SettingValue | '') =>
      setState((current) => ({
        ...current,
        draft: { ...current.draft, [key]: value },
        saved: false,
        discard: false,
      })),
    cancel: () => update({ draft: state.settings?.values ?? {}, saved: false, error: null }),
  };
}

export type PreferencesEditor = ReturnType<typeof useBackupPreferences>;
