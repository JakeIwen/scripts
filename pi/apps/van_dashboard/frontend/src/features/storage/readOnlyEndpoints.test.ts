import { describe, expect, it, vi } from 'vitest';

import { fetchBackupStatus } from '../backups/api';
import { backupStatusPayload } from '../backups/testFixtures';
import { fetchUsbStatus } from '../usb/api';
import { usbStatusPayload } from '../usb/testFixtures';
import { fetchDiskStatus, fetchStoragePolicy } from './api';
import { diskStatusPayload, storagePolicyPayload } from './testFixtures';

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('storage, USB, and backup preview endpoints', () => {
  it('uses only the four audited read-only GET routes', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse(storagePolicyPayload()))
      .mockResolvedValueOnce(jsonResponse(diskStatusPayload()))
      .mockResolvedValueOnce(jsonResponse(usbStatusPayload()))
      .mockResolvedValueOnce(jsonResponse(backupStatusPayload()));
    const signal = new AbortController().signal;

    await fetchStoragePolicy(signal);
    await fetchDiskStatus(signal);
    await fetchUsbStatus(signal);
    await fetchBackupStatus(signal);

    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      '/api/storage-policy',
      '/api/disks',
      '/api/usb-devices',
      '/api/backups',
    ]);
    for (const [, options] of fetchMock.mock.calls) {
      expect(options?.method).toBeUndefined();
      expect(options?.cache).toBe('no-store');
    }
  });
});
