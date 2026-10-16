import { useEffect, useState, type FormEvent } from 'react';
import { useSingleFlightAction } from '../../hooks/useSingleFlightAction';
import { fetchBackupPreferences, saveBackupPreferences, type BackupPreferences } from './settings';
import { prepareSettingsDraft, type SettingsDraft, type SettingDraftValue } from './settingsDraft';

interface PreferenceState {
  settings: BackupPreferences | null;
  draft: SettingsDraft;
  error: string | null;
  saved: boolean;
  discard: boolean;
}
const initial: PreferenceState = {
  settings: null,
  draft: {},
  error: null,
  saved: false,
  discard: false,
};

export function useBackupPreferences(refresh: () => Promise<unknown>, onClose: () => void) {
  const [state, setState] = useState(initial);
  const { running: busy, run: runAction } = useSingleFlightAction();
  const update = (patch: Partial<PreferenceState>) =>
    setState((current) => ({ ...current, ...patch }));
  const accept = (settings: BackupPreferences, saved = false) =>
    update({ settings, draft: settings.values, error: null, discard: false, saved });
  const { changes, errors: fieldErrors, count } = prepareSettingsDraft(state.settings, state.draft);
  const canSave = !!count && !busy && !Object.keys(fieldErrors).length;
  useEffect(() => {
    const abort = new AbortController();
    fetchBackupPreferences(abort.signal)
      .then((settings) => accept(settings))
      .catch((reason) => {
        if (!abort.signal.aborted)
          update({ error: reason instanceof Error ? reason.message : 'Settings unavailable.' });
      });
    return () => abort.abort();
  }, []);
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!state.settings || !canSave) return;
    const revision = state.settings.revision;
    await runAction(async () => {
      update({ error: null, saved: false });
      try {
        accept(await saveBackupPreferences(revision, changes), true);
        await refresh();
      } catch (reason) {
        update({
          error: reason instanceof Error ? reason.message : 'Settings could not be saved.',
        });
      }
    });
  }
  async function reload() {
    await runAction(async () => {
      update({ error: null });
      try {
        accept(await fetchBackupPreferences());
      } catch (reason) {
        update({ error: reason instanceof Error ? reason.message : 'Settings unavailable.' });
      }
    });
  }
  function close() {
    if (busy) return;
    if (count) update({ discard: true });
    else onClose();
  }
  return {
    ...state,
    busy,
    runAction,
    fieldErrors,
    changes,
    count,
    canSave,
    save,
    reload,
    close,
    keepEditing: () => update({ discard: false }),
    change: (key: string, value: SettingDraftValue) =>
      setState((current) => ({
        ...current,
        draft: { ...current.draft, [key]: value },
        saved: false,
        discard: false,
      })),
    cancel: () =>
      update({ draft: state.settings?.values ?? {}, saved: false, error: null, discard: false }),
  };
}

export type PreferencesEditor = ReturnType<typeof useBackupPreferences>;
