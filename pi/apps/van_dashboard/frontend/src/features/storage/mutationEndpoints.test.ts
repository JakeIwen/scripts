import { describe, expect, it, vi } from 'vitest';

import { setCloneCardNominalGb, startBackup, startBackupClone, stopBackup } from '../backups/api';
import { backupStatusPayload } from '../backups/testFixtures';
import { discoverUsbPorts, recoverUsb2, startUsbPortAction } from '../usb/api';
import { usbStatusPayload } from '../usb/testFixtures';
import { startDiskAction, updateStoragePolicy } from './api';
import { diskStatusPayload, storagePolicyPayload } from './testFixtures';

function jsonResponse(payload: Record<string, unknown>, message: string): Response {
  return new Response(JSON.stringify({ ...payload, message }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function usbMutationPayload(): Record<string, unknown> {
  const payload = usbStatusPayload();
  return {
    ok: true,
    usb_ports: payload.usb_ports,
  };
}

describe('storage, USB, and backup mutation forms', () => {
  it('sends only exact URL-encoded fields and the dashboard control header', async () => {
    const policy = storagePolicyPayload();
    const disks = diskStatusPayload();
    const usb = usbMutationPayload();
    const backups = backupStatusPayload(true);
    const responses = [
      jsonResponse(policy, 'policy updated'),
      jsonResponse(disks, 'disk action started'),
      jsonResponse(usb, 'controls loaded'),
      jsonResponse(usb, 'port enabled'),
      jsonResponse(usb, 'port disabled'),
      jsonResponse(usb, 'port cycling'),
      jsonResponse(usb, 'USB 2 recovery started'),
      jsonResponse(backups, 'Borg started'),
      jsonResponse(backups, 'EXFAT started'),
      jsonResponse(backups, 'Borg stopping'),
      jsonResponse(backups, 'EXFAT stopping'),
      jsonResponse(backups, 'clone started'),
      jsonResponse(backups, 'capacity updated'),
    ];
    const fetchMock = vi.spyOn(globalThis, 'fetch');
    responses.forEach((response) => fetchMock.mockResolvedValueOnce(response));

    await updateStoragePolicy('torrents_enabled', false);
    await startDiskAction('movingparts', 'eject');
    await discoverUsbPorts();
    await startUsbPortAction('2-2:1', 'on');
    await startUsbPortAction('2-2:1', 'off');
    await startUsbPortAction('2-2:1', 'cycle');
    await recoverUsb2();
    await startBackup('borg');
    await startBackup('exfat');
    await stopBackup('borg');
    await stopBackup('exfat');
    await startBackupClone('hotspare-a');
    await setCloneCardNominalGb(128);

    expect(
      fetchMock.mock.calls.map(([path, options]) => [
        path,
        (options?.body as URLSearchParams).toString(),
      ]),
    ).toEqual([
      ['/api/storage-policy', 'field=torrents_enabled&value=false'],
      ['/api/disks/action', 'label=movingparts&action=eject'],
      ['/api/usb-ports/discover', ''],
      ['/api/usb-ports/action', 'port=2-2%3A1&action=on'],
      ['/api/usb-ports/action', 'port=2-2%3A1&action=off'],
      ['/api/usb-ports/action', 'port=2-2%3A1&action=cycle'],
      ['/api/usb-ports/recover', ''],
      ['/api/backups/borg', ''],
      ['/api/backups/exfat', ''],
      ['/api/backups/borg/stop', ''],
      ['/api/backups/exfat/stop', ''],
      ['/api/backups/clone', 'target=hotspare-a'],
      ['/api/backups/settings/clone-card-size', 'nominal_gb=128'],
    ]);
    for (const [, options] of fetchMock.mock.calls) {
      expect(options).toMatchObject({
        method: 'POST',
        cache: 'no-store',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
          'X-Van-Dashboard': '1',
        },
      });
    }
  });
});
