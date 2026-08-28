import { postForm } from '../../api/client';
import { messageFrom, objectValue } from '../../api/validation';
import {
  decodeCopAlertRequest,
  decodeStarlinkStatus,
  type CopAlertRequest,
  type StarlinkStatus,
} from './dashboardStatus';

export async function setCopAlert(active: boolean): Promise<{
  message: string;
  request: CopAlertRequest;
}> {
  const payload = await postForm('/api/cop-alert', { active: active ? 'true' : 'false' });
  const response = objectValue(payload, 'COP ALERT response');
  if (response.ok !== true) throw new TypeError('COP ALERT response.ok must be true');
  const request = decodeCopAlertRequest(response.cop_alert);
  if (request.requested !== active) {
    throw new TypeError('COP ALERT response disagrees with the requested state');
  }
  return {
    message: messageFrom(response) ?? `COP ALERT ${active ? 'armed' : 'disarmed'}`,
    request,
  };
}

export async function toggleStarlinkPower(): Promise<{
  message: string;
  status: StarlinkStatus;
}> {
  const payload = await postForm('/api/starlink');
  const response = objectValue(payload, 'Starlink response');
  return {
    message: messageFrom(response) ?? 'Starlink power changed',
    status: decodeStarlinkStatus(response.starlink),
  };
}
