import { useEffect, useState } from 'react';
import { useSingleFlightAction } from '../../hooks/useSingleFlightAction';
import { useToast } from '../../components/ToastProvider';
import { fetchBackupPreferences, saveBackupPreferences, type BackupPreferences } from './settings';
import { prepareSettingsDraft, type SettingsDraft, type SettingDraftValue } from './settingsDraft';

interface PreferenceState {
  settings: BackupPreferences | null;
  draft: SettingsDraft;
  error: string | null;
}
const initial: PreferenceState = { settings: null, draft: {}, error: null };

export function useBackupPreferences(open: boolean, refresh: () => Promise<unknown>) {
  const [state, setState] = useState(initial);
  const { running: busy, run: runAction } = useSingleFlightAction();
  const { showToast } = useToast();
  const { errors: fieldErrors, count } = prepareSettingsDraft(state.settings, state.draft);
  const update = (patch: Partial<PreferenceState>) =>
    setState((current) => ({ ...current, ...patch }));
  useEffect(() => {
    if (open) void reload();
  }, [open]);

  async function commit(key: string, value = state.draft[key]) {
    const settings = state.settings;
    const field = settings?.fields.find((field) => field.id === key);
    if (!settings || !field || value === undefined) return;
    const { changes, errors } = prepareSettingsDraft(settings, {
      ...settings.values,
      [key]: value,
    });
    if (errors[key]) {
      showToast(`Not saved. ${errors[key]}`, 'error');
      return;
    }
    if (!Object.keys(changes).length) return;
    await runAction(async () => {
      update({ error: null });
      try {
        const saved = await saveBackupPreferences(settings.revision, changes);
        setState((current) => ({
          settings: saved,
          draft: { ...current.draft, [key]: saved.values[key] ?? value },
          error: null,
        }));
      } catch (reason) {
        const message = reason instanceof Error ? reason.message : 'Setting could not be saved.';
        update({ error: message });
        showToast(message, 'error');
        return;
      }
      const group = settings.groups.find((group) => group.id === field.group)?.label;
      showToast(`${group} · ${field.label} updated`);
      // The write is already confirmed; a status refresh failure must not report it as unsaved.
      await refresh().catch(() => showToast('Setting saved; dashboard refresh failed.', 'error'));
    });
  }
  async function reload() {
    await runAction(async () => {
      update({ error: null });
      try {
        const settings = await fetchBackupPreferences();
        update({ settings, draft: settings.values });
      } catch (reason) {
        update({ error: reason instanceof Error ? reason.message : 'Settings unavailable.' });
      }
    });
  }
  return {
    ...state,
    busy,
    runAction,
    fieldErrors,
    count,
    commit,
    reload,
    change: (key: string, value: SettingDraftValue) =>
      setState((current) => ({ ...current, draft: { ...current.draft, [key]: value } })),
  };
}

export type PreferencesEditor = ReturnType<typeof useBackupPreferences>;
