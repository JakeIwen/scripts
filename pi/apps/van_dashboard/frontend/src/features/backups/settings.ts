import { getJson, postJson } from '../../api/client';
import {
  arrayValue,
  booleanValue,
  numberValue,
  objectValue,
  stringValue,
} from '../../api/validation';

const FIELD_KINDS = ['number', 'bandwidth', 'hour', 'toggle'] as const;
export type SettingValue = number | boolean;
export interface BackupSettingField {
  id: string;
  group: string;
  section: string;
  label: string;
  unit: string;
  help: string;
  minimum: number;
  maximum: number;
  kind: (typeof FIELD_KINDS)[number];
}
export interface BackupPreferences {
  revision: string;
  timezone: string;
  values: Record<string, SettingValue>;
  fields: BackupSettingField[];
  groups: { id: string; label: string }[];
}

export function decodeBackupPreferences(payload: unknown): BackupPreferences {
  const response = objectValue(payload, 'backup settings response');
  if (response.ok !== true) throw new TypeError('Backup settings response was not successful');
  const row = objectValue(response.settings, 'backup settings');
  const fields = arrayValue(row.fields, 'backup fields').map((value): BackupSettingField => {
    const field = objectValue(value, 'backup field');
    const rawKind = stringValue(field.kind, 'setting kind');
    const kind = FIELD_KINDS.find((choice) => choice === rawKind);
    if (!kind) throw new TypeError('Unknown backup setting kind');
    return {
      id: stringValue(field.id, 'setting id'),
      group: stringValue(field.group, 'setting group'),
      section: stringValue(field.section, 'setting section'),
      label: stringValue(field.label, 'setting label'),
      unit: stringValue(field.unit, 'setting unit'),
      help: stringValue(field.help, 'setting help'),
      minimum: numberValue(field.minimum, 'setting minimum'),
      maximum: numberValue(field.maximum, 'setting maximum'),
      kind,
    };
  });
  const raw = objectValue(row.values, 'setting values');
  const values = Object.fromEntries(
    fields.map((field) => [
      field.id,
      field.kind === 'toggle'
        ? booleanValue(raw[field.id], field.label)
        : numberValue(raw[field.id], field.label),
    ]),
  );
  const settings: BackupPreferences = {
    revision: stringValue(row.revision, 'settings revision'),
    timezone: stringValue(row.timezone, 'settings timezone'),
    fields,
    values,
    groups: arrayValue(row.groups, 'setting groups').map((value) => {
      const group = objectValue(value, 'setting group');
      return {
        id: stringValue(group.id, 'setting group id'),
        label: stringValue(group.label, 'setting group label'),
      };
    }),
  };
  validateDefinitions(settings);
  return settings;
}

function validateDefinitions(settings: BackupPreferences) {
  const groups = new Set(settings.groups.map((group) => group.id));
  const fields = new Set(settings.fields.map((field) => field.id));
  if (groups.size !== settings.groups.length || settings.groups.some((group) => !group.id))
    throw new TypeError('Backup setting groups must have unique, nonempty identifiers');
  if (fields.size !== settings.fields.length || settings.fields.some((field) => !field.id))
    throw new TypeError('Backup settings must have unique, nonempty identifiers');
  for (const field of settings.fields) {
    if (!groups.has(field.group) || field.minimum > field.maximum)
      throw new TypeError('Backup setting has an unknown group or invalid bounds');
    if (
      field.kind === 'hour' &&
      (!Number.isInteger(field.minimum) ||
        !Number.isInteger(field.maximum) ||
        field.minimum < 0 ||
        field.maximum > 23)
    )
      throw new TypeError('Backup hour settings must stay within 00:00–23:00');
    if (!settingValueMatches(field, settings.values[field.id]))
      throw new TypeError(`Backup setting ${field.id} has an invalid value`);
  }
}

export function settingValueMatches(
  field: BackupSettingField,
  value: unknown,
): value is SettingValue {
  if (field.kind === 'toggle') return typeof value === 'boolean';
  if (
    typeof value !== 'number' ||
    !Number.isFinite(value) ||
    value < field.minimum ||
    value > field.maximum
  )
    return false;
  // The worker encodes fractional MiB/s limits as an integral number of KiB/s.
  return Number.isInteger(field.kind === 'bandwidth' ? value * 1024 : value);
}

export async function fetchBackupPreferences(signal?: AbortSignal) {
  return decodeBackupPreferences(await getJson('/api/backups/settings', signal));
}
export async function saveBackupPreferences(
  revision: string,
  changes: Record<string, SettingValue>,
) {
  return decodeBackupPreferences(await postJson('/api/backups/settings', { revision, changes }));
}
