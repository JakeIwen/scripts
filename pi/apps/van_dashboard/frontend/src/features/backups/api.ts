import { getJson, postForm } from '../../api/client';
import { objectValue, stringValue } from '../../api/validation';
import { decodeBackupStatusResponse } from './decoders';
import type { BackupMutationResult, BackupStatus, BackupStopKind } from './types';

export async function fetchBackupStatus(signal: AbortSignal): Promise<BackupStatus> {
  const payload = await getJson('/api/backups', signal);
  return decodeBackupStatusResponse(payload);
}

function decodeMutation(payload: unknown, label: string): BackupMutationResult {
  const response = objectValue(payload, label);
  if (response.ok !== true) throw new TypeError(`${label}.ok must be true`);
  return {
    message: stringValue(response.message, `${label}.message`),
    backups: decodeBackupStatusResponse(payload),
  };
}

export async function startBackup(kind: BackupStopKind): Promise<BackupMutationResult> {
  return decodeMutation(await postForm(`/api/backups/${kind}`), `${kind} backup response`);
}

export async function stopBackup(kind: BackupStopKind): Promise<BackupMutationResult> {
  return decodeMutation(
    await postForm(`/api/backups/${kind}/stop`),
    `${kind} backup-stop response`,
  );
}

export async function startBackupClone(target: string): Promise<BackupMutationResult> {
  const label = target.trim();
  if (!label) throw new TypeError('hotspare target must not be empty');
  return decodeMutation(
    await postForm('/api/backups/clone', { target: label }),
    'backup clone response',
  );
}
