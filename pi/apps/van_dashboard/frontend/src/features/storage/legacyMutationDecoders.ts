import { objectValue, stringValue } from '../../api/validation';
import { decodeDiskStatusResponse, decodeStoragePolicyResponse } from './decoders';
import type { DiskMutationResult, StoragePolicyMutationResult } from './types';

function trueValue(value: unknown, label: string): true {
  if (value !== true) throw new TypeError(`${label} must be true`);
  return true;
}

function mutationMessage(payload: unknown, label: string): string {
  const response = objectValue(payload, label);
  trueValue(response.ok, `${label}.ok`);
  return stringValue(response.message, `${label}.message`);
}

/** The old API payload-only extraction, retained as the Stage A parity oracle. */
export function decodeStoragePolicyMutationPayload(value: unknown): StoragePolicyMutationResult {
  return {
    message: mutationMessage(value, 'storage policy mutation response'),
    policy: decodeStoragePolicyResponse(value),
  };
}

/** The old API payload-only extraction, retained as the Stage A parity oracle. */
export function decodeDiskMutationPayload(value: unknown): DiskMutationResult {
  return {
    message: mutationMessage(value, 'disk mutation response'),
    diskStatus: decodeDiskStatusResponse(value),
  };
}
