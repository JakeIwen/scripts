import { postForm } from '../../api/client';
import { booleanValue, objectValue, stringValue } from '../../api/validation';

export interface TelemetryMutationResult {
  message: string;
}

function successfulResponse(payload: unknown, label: string): Record<string, unknown> {
  const response = objectValue(payload, label);
  if (response.ok !== true) throw new TypeError(`${label}.ok must be true`);
  return response;
}

/** Toggle the service. The backend deliberately accepts no form fields. */
export async function toggleTelemetryService(): Promise<TelemetryMutationResult> {
  const payload = await postForm('/api/telemetry-service');
  const response = successfulResponse(payload, 'telemetry service response');
  const service = objectValue(response.service, 'telemetry service response.service');

  booleanValue(service.available, 'telemetry service response.service.available');
  booleanValue(service.running, 'telemetry service response.service.running');

  return {
    message: stringValue(response.message, 'telemetry service response.message'),
  };
}

/** Start the guarded voltage check. Its progress is read from telemetry-summary. */
export async function startTelemetryVoltageCheck(): Promise<TelemetryMutationResult> {
  const payload = await postForm('/api/telemetry-voltage-check');
  const response = successfulResponse(payload, 'voltage check response');
  const check = objectValue(response.check, 'voltage check response.check');

  if (check.status !== 'running') {
    throw new TypeError('voltage check response.check.status must be running');
  }

  return {
    message: stringValue(response.message, 'voltage check response.message'),
  };
}
