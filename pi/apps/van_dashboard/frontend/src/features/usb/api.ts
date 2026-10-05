import { getJson, postForm } from '../../api/client';
import { decodeMutation, decodeUsbStatusResponse } from './schema';
import type { UsbPortAction, UsbPortMutationResult, UsbStatus } from './types';

export async function fetchUsbStatus(signal: AbortSignal): Promise<UsbStatus> {
  const payload = await getJson('/api/usb-devices', signal);
  return decodeUsbStatusResponse(payload);
}

export async function discoverUsbPorts(): Promise<UsbPortMutationResult> {
  return decodeMutation(await postForm('/api/usb-ports/discover'), 'USB discovery response');
}

export async function startUsbPortAction(
  port: string,
  action: Exclude<UsbPortAction, 'restore'>,
): Promise<UsbPortMutationResult> {
  const key = port.trim();
  if (!key) throw new TypeError('USB port key must not be empty');
  return decodeMutation(
    await postForm('/api/usb-ports/action', { port: key, action }),
    'USB port-action response',
  );
}

export async function recoverUsb2(): Promise<UsbPortMutationResult> {
  return decodeMutation(await postForm('/api/usb-ports/recover'), 'USB recovery response');
}
