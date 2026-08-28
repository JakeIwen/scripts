import { getJson, postForm } from '../../api/client';
import { objectValue, stringValue } from '../../api/validation';
import { decodeDiskStatusResponse, decodeStoragePolicyResponse } from './decoders';
import type {
  DiskAction,
  DiskMutationResult,
  DiskStatus,
  StoragePolicy,
  StoragePolicyField,
  StoragePolicyMutationResult,
} from './types';

export async function fetchStoragePolicy(signal: AbortSignal): Promise<StoragePolicy> {
  const payload = await getJson('/api/storage-policy', signal);
  return decodeStoragePolicyResponse(payload);
}

export async function fetchDiskStatus(signal: AbortSignal): Promise<DiskStatus> {
  const payload = await getJson('/api/disks', signal);
  return decodeDiskStatusResponse(payload);
}

function trueValue(value: unknown, label: string): true {
  if (value !== true) throw new TypeError(`${label} must be true`);
  return true;
}

function mutationMessage(payload: unknown, label: string): string {
  const response = objectValue(payload, label);
  trueValue(response.ok, `${label}.ok`);
  return stringValue(response.message, `${label}.message`);
}

export async function updateStoragePolicy(
  field: StoragePolicyField,
  enabled: boolean,
): Promise<StoragePolicyMutationResult> {
  const payload = await postForm('/api/storage-policy', {
    field,
    value: enabled ? 'true' : 'false',
  });
  return {
    message: mutationMessage(payload, 'storage policy mutation response'),
    policy: decodeStoragePolicyResponse(payload),
  };
}

export async function startDiskAction(
  label: string,
  action: DiskAction,
): Promise<DiskMutationResult> {
  const diskLabel = label.trim();
  if (!diskLabel) throw new TypeError('disk label must not be empty');
  const payload = await postForm('/api/disks/action', { label: diskLabel, action });
  return {
    message: mutationMessage(payload, 'disk mutation response'),
    diskStatus: decodeDiskStatusResponse(payload),
  };
}
