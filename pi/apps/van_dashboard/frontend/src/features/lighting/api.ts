import { getJson, postForm } from '../../api/client';
import { objectValue, stringValue } from '../../api/validation';
import { decodeLightingStatus } from './decoders';
import type { LightingMutationResult, LightingStatus } from './types';

export async function fetchLightingStatus(signal: AbortSignal): Promise<LightingStatus> {
  const payload = await getJson('/api/lights', signal);
  return decodeLightingStatus(payload);
}

function decodeLightingMutation(payload: unknown): LightingMutationResult {
  const response = objectValue(payload, 'lighting mutation response');
  return {
    message: stringValue(response.message, 'lighting mutation response.message'),
    lighting: decodeLightingStatus(payload),
  };
}

export async function setLightingPower(
  target: string,
  enabled: boolean,
): Promise<LightingMutationResult> {
  const payload = await postForm('/api/lights/power', {
    target,
    value: enabled ? 'true' : 'false',
  });
  return decodeLightingMutation(payload);
}

export async function setLightBrightness(
  entityId: string,
  brightness: number,
): Promise<LightingMutationResult> {
  const payload = await postForm('/api/lights/brightness', {
    entity: entityId,
    brightness,
  });
  return decodeLightingMutation(payload);
}

export async function setLightHue(entityId: string, hue: number): Promise<LightingMutationResult> {
  const payload = await postForm('/api/lights/hue', { entity: entityId, hue });
  return decodeLightingMutation(payload);
}

export async function setLightColorTemperature(
  entityId: string,
  kelvin: number,
): Promise<LightingMutationResult> {
  const payload = await postForm('/api/lights/color-temperature', {
    entity: entityId,
    kelvin,
  });
  return decodeLightingMutation(payload);
}
