import { getJson, postForm } from '../../api/client';
import { booleanValue, numberValue, objectValue, stringValue } from '../../api/validation';
import { decodeSonosStatus } from './decoders';
import type {
  SonosMuteResult,
  SonosMutationResult,
  SonosSelectionResult,
  SonosStatus,
  SonosTransportAction,
  SonosVolumeResult,
} from './types';

export async function fetchSonosStatus(signal: AbortSignal): Promise<SonosStatus> {
  const payload = await getJson('/api/speakers', signal);
  return decodeSonosStatus(payload);
}

function trueValue(value: unknown, label: string): true {
  if (value !== true) throw new TypeError(`${label} must be true`);
  return true;
}

function decodeMessage(value: unknown, label: string): SonosMutationResult {
  const response = objectValue(value, label);
  trueValue(response.ok, `${label}.ok`);
  return { message: stringValue(response.message, `${label}.message`) };
}

function volumeValue(value: unknown, label: string): number {
  const volume = numberValue(value, label);
  if (!Number.isInteger(volume) || volume < 0 || volume > 100) {
    throw new TypeError(`${label} must be a whole number from 0 to 100`);
  }
  return volume;
}

function requestedVolume(volume: number): number {
  return volumeValue(volume, 'requested Sonos volume');
}

function speakerName(name: string): string {
  const normalized = name.trim();
  if (!normalized) throw new TypeError('Sonos speaker name must not be empty');
  return normalized;
}

export async function selectSonosCoordinator(name: string): Promise<SonosSelectionResult> {
  const payload = await postForm('/api/speakers/select', { name: speakerName(name) });
  const result = decodeMessage(payload, 'Sonos selection response');
  const response = objectValue(payload, 'Sonos selection response');
  return {
    ...result,
    device: stringValue(response.device, 'Sonos selection response.device'),
  };
}

export async function setSonosGrouping(
  name: string,
  grouped: boolean,
): Promise<SonosMutationResult> {
  const payload = await postForm('/api/speakers/group', {
    name: speakerName(name),
    grouped: grouped ? '1' : '0',
  });
  return decodeMessage(payload, 'Sonos grouping response');
}

export async function setSonosSpeakerVolume(
  name: string,
  volume: number,
): Promise<SonosVolumeResult> {
  const payload = await postForm('/api/speakers/volume', {
    name: speakerName(name),
    volume: requestedVolume(volume),
  });
  const result = decodeMessage(payload, 'Sonos speaker-volume response');
  const response = objectValue(payload, 'Sonos speaker-volume response');
  return {
    ...result,
    volume: volumeValue(response.volume, 'Sonos speaker-volume response.volume'),
  };
}

export async function setSonosSpeakerMuted(name: string, muted: boolean): Promise<SonosMuteResult> {
  const payload = await postForm('/api/speakers/mute', {
    name: speakerName(name),
    muted: muted ? '1' : '0',
  });
  const result = decodeMessage(payload, 'Sonos speaker-mute response');
  const response = objectValue(payload, 'Sonos speaker-mute response');
  return {
    ...result,
    muted: booleanValue(response.muted, 'Sonos speaker-mute response.muted'),
  };
}

export async function setSonosGroupVolume(volume: number): Promise<SonosVolumeResult> {
  const payload = await postForm('/api/speakers/group-volume', {
    volume: requestedVolume(volume),
  });
  const result = decodeMessage(payload, 'Sonos group-volume response');
  const response = objectValue(payload, 'Sonos group-volume response');
  return {
    ...result,
    volume: volumeValue(response.volume, 'Sonos group-volume response.volume'),
  };
}

export async function setSonosGroupMuted(muted: boolean): Promise<SonosMuteResult> {
  const payload = await postForm('/api/speakers/group-mute', { muted: muted ? '1' : '0' });
  const result = decodeMessage(payload, 'Sonos group-mute response');
  const response = objectValue(payload, 'Sonos group-mute response');
  return {
    ...result,
    muted: booleanValue(response.muted, 'Sonos group-mute response.muted'),
  };
}

export async function controlSonosTransport(
  action: SonosTransportAction,
): Promise<SonosMutationResult> {
  const payload = await postForm('/api/speakers/transport', { action });
  return decodeMessage(payload, 'Sonos transport response');
}
