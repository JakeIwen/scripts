import { settingValueMatches, type BackupPreferences, type SettingValue } from './settings';

export type SettingDraftValue = SettingValue | '';
export type SettingsDraft = Record<string, SettingDraftValue>;

export function prepareSettingsDraft(settings: BackupPreferences | null, draft: SettingsDraft) {
  const changes: Record<string, SettingValue> = {};
  const errors: Record<string, string> = {};
  let count = 0;
  for (const field of settings?.fields ?? []) {
    const value = draft[field.id];
    const changed = value !== settings?.values[field.id];
    if (changed) count++;
    if (!settingValueMatches(field, value)) {
      errors[field.id] =
        field.kind === 'toggle'
          ? 'Choose on or off.'
          : `${field.label}: enter ${field.minimum}–${field.maximum} ${field.unit}${field.kind === 'bandwidth' ? ' in whole KiB/s increments' : ' as a whole number'}.`;
    } else if (changed) changes[field.id] = value;
  }
  return { changes, errors, count };
}
