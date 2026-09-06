import {
  arrayValue,
  booleanValue,
  nullableBoolean,
  nullableString,
  objectValue,
  stringValue,
} from '../../api/validation';
import type { SonosNowPlaying, SonosSpeaker, SonosStatus, SonosTransportState } from './types';

const TRANSPORT_STATES = [
  'PLAYING',
  'PAUSED_PLAYBACK',
  'STOPPED',
  'TRANSITIONING',
  'NO_MEDIA_PRESENT',
  'UNKNOWN',
] as const satisfies readonly SonosTransportState[];

function trueValue(value: unknown, label: string): true {
  if (value !== true) throw new TypeError(`${label} must be true`);
  return true;
}

function nonemptyString(value: unknown, label: string): string {
  const text = stringValue(value, label);
  if (!text.trim()) throw new TypeError(`${label} must not be empty`);
  return text;
}

function nullablePercentage(value: unknown, label: string): number | null {
  if (value === null) return null;
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 0 || value > 100) {
    throw new TypeError(`${label} must be a whole number from 0 to 100 or null`);
  }
  return value;
}

function transportState(value: unknown): SonosTransportState {
  const state = stringValue(value, 'now_playing.transport_state');
  if (!TRANSPORT_STATES.includes(state as SonosTransportState)) {
    throw new TypeError(`now_playing.transport_state has an unsupported value: ${state}`);
  }
  return state as SonosTransportState;
}

/** Parse Sonos H:MM:SS or MM:SS clock text at the API boundary. */
export function decodeSonosClock(value: unknown, label: string): number | null {
  const text = stringValue(value, label);
  // Sonos/UPnP uses NOT_IMPLEMENTED when an idle queue has no meaningful
  // position or duration. Treat it as absent progress, not a broken speaker.
  if (text === '' || text === 'NOT_IMPLEMENTED') return null;

  const parts = text.split(':');
  if ((parts.length !== 2 && parts.length !== 3) || parts.some((part) => !/^\d+$/.test(part))) {
    throw new TypeError(`${label} must use H:MM:SS or MM:SS clock text`);
  }

  const values = parts.map(Number);
  const seconds = values.at(-1);
  const minutes = values.at(-2);
  if (seconds === undefined || minutes === undefined || seconds > 59 || minutes > 59) {
    throw new TypeError(`${label} contains an invalid clock value`);
  }

  return values.reduce((total, part) => total * 60 + part, 0);
}

function decodeNowPlaying(value: unknown): SonosNowPlaying {
  const object = objectValue(value, 'now_playing');
  const albumArt = nullableString(object.album_art, 'now_playing.album_art');
  if (albumArt !== null && !/^\/api\/speakers\/art\/[0-9a-f]{16}$/.test(albumArt)) {
    throw new TypeError('now_playing.album_art must be a dashboard Sonos-art path');
  }

  return {
    title: nonemptyString(object.title, 'now_playing.title'),
    artist: stringValue(object.artist, 'now_playing.artist'),
    album: stringValue(object.album, 'now_playing.album'),
    positionSeconds: decodeSonosClock(object.position, 'now_playing.position'),
    durationSeconds: decodeSonosClock(object.duration, 'now_playing.duration'),
    transportState: transportState(object.transport_state),
    albumArt,
  };
}

function decodeSpeaker(value: unknown, index: number): SonosSpeaker {
  const label = `speakers[${index}]`;
  const object = objectValue(value, label);
  return {
    name: nonemptyString(object.name, `${label}.name`),
    volume: nullablePercentage(object.volume, `${label}.volume`),
    muted: nullableBoolean(object.muted, `${label}.muted`),
    grouped: booleanValue(object.grouped, `${label}.grouped`),
    coordinator: booleanValue(object.coordinator, `${label}.coordinator`),
    groupCoordinator: nonemptyString(object.group_coordinator, `${label}.group_coordinator`),
  };
}

/** Decode and cross-check the complete GET /api/speakers snapshot. */
export function decodeSonosStatus(payload: unknown): SonosStatus {
  const response = objectValue(payload, 'Sonos response');
  trueValue(response.ok, 'Sonos response.ok');
  const coordinator = nonemptyString(response.coordinator, 'Sonos response.coordinator');
  const group = objectValue(response.group, 'Sonos response.group');
  const speakers = arrayValue(response.speakers, 'Sonos response.speakers').map(decodeSpeaker);

  if (speakers.length === 0) {
    throw new TypeError('Sonos response.speakers must contain at least one speaker');
  }
  const names = new Set(speakers.map((speaker) => speaker.name));
  if (names.size !== speakers.length) {
    throw new TypeError('Sonos response.speakers contains duplicate names');
  }
  if (!names.has(coordinator)) {
    throw new TypeError('Sonos response.coordinator must name a returned speaker');
  }
  if (speakers.some((speaker) => !names.has(speaker.groupCoordinator))) {
    throw new TypeError('each Sonos group coordinator must name a returned speaker');
  }

  const coordinators = speakers.filter((speaker) => speaker.coordinator);
  if (coordinators.length !== 1 || coordinators[0]?.name !== coordinator) {
    throw new TypeError('Sonos coordinator flags do not match response.coordinator');
  }
  if (!coordinators[0].grouped) {
    throw new TypeError('the active Sonos coordinator must belong to its group');
  }

  return {
    coordinator,
    group: {
      volume: nullablePercentage(group.volume, 'group.volume'),
      muted: nullableBoolean(group.muted, 'group.muted'),
    },
    nowPlaying: decodeNowPlaying(response.now_playing),
    speakers,
  };
}
