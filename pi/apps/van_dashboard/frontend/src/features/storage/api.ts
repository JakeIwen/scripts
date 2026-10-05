import { getJson, postForm } from '../../api/client';
import {
  decodeDiskMutation,
  decodeDiskStatusResponse,
  decodeStoragePolicyMutation,
  decodeStoragePolicyResponse,
} from './schema';
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

export async function updateStoragePolicy(
  field: StoragePolicyField,
  enabled: boolean,
): Promise<StoragePolicyMutationResult> {
  const payload = await postForm('/api/storage-policy', {
    field,
    value: enabled ? 'true' : 'false',
  });
  return decodeStoragePolicyMutation(payload);
}

export async function startDiskAction(
  label: string,
  action: DiskAction,
): Promise<DiskMutationResult> {
  const diskLabel = label.trim();
  if (!diskLabel) throw new TypeError('disk label must not be empty');
  const payload = await postForm('/api/disks/action', { label: diskLabel, action });
  return decodeDiskMutation(payload);
}
