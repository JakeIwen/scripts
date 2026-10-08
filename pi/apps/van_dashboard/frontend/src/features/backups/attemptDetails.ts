import { getJson } from '../../api/client';
import {
  arrayValue,
  nullableString,
  numberValue,
  objectValue,
  stringValue,
} from '../../api/validation';
import { decodeICloudAttempt, type ICloudAttempt, type ICloudBackupKind } from './icloud';

export interface ICloudAttemptDetails {
  attempt: ICloudAttempt;
  entries: { at: number; message: string; explanation: string }[];
  note: string | null;
}

export async function loadAttemptDetails(
  kind: ICloudBackupKind,
  id: string,
  signal: AbortSignal,
): Promise<ICloudAttemptDetails> {
  const response = objectValue(
    await getJson(`/api/backups/cloud/${kind}/attempts/${encodeURIComponent(id)}`, signal),
    'backup details response',
  );
  const details = objectValue(response.details, 'backup details');
  return {
    attempt: decodeICloudAttempt(details.attempt),
    entries: arrayValue(details.entries, 'backup journal entries').map((value) => {
      const row = objectValue(value, 'backup journal entry');
      return {
        at: numberValue(row.at, 'journal timestamp'),
        message: stringValue(row.message, 'journal message'),
        explanation: stringValue(row.explanation, 'journal explanation'),
      };
    }),
    note: nullableString(details.note, 'backup journal note'),
  };
}
