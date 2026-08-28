import { getJson, postForm } from '../../api/client';
import { objectValue, stringValue } from '../../api/validation';
import { decodeUbntWifiStatus } from './decoder';
import type {
  UbntMutationResult,
  UbntOperationKind,
  UbntProfileUpdate,
  UbntProvisionRequest,
  UbntWifiStatus,
} from './types';

export async function fetchUbntWifiStatus(signal: AbortSignal): Promise<UbntWifiStatus> {
  const payload = await getJson('/api/ubnt-wifi', signal);
  return decodeUbntWifiStatus(payload);
}

function utf8Length(value: string): number {
  return new TextEncoder().encode(value).length;
}

function hasControlCharacter(value: string): boolean {
  return [...value].some((character) => {
    const code = character.codePointAt(0) ?? 0;
    return code < 32 || code === 127;
  });
}

function profileName(value: string): string {
  if (!value || utf8Length(value) > 128 || hasControlCharacter(value)) {
    throw new TypeError('UBNT profile name is invalid');
  }
  return value;
}

function bssid(value: string, allowBlank: boolean): string {
  const normalized = value.trim().toUpperCase();
  if (allowBlank && normalized === '') return '';
  if (!/^(?:[0-9A-F]{2}:){5}[0-9A-F]{2}$/.test(normalized)) {
    throw new TypeError('UBNT access-point address must be a MAC address');
  }
  return normalized;
}

function password(value: string, allowBlank: boolean): string {
  const size = utf8Length(value);
  if (hasControlCharacter(value) || (!allowBlank && (size < 8 || size > 63))) {
    throw new TypeError('WPA password must be 8 to 63 bytes without control characters');
  }
  if (allowBlank && value && (size < 8 || size > 63)) {
    throw new TypeError('WPA password must be blank or 8 to 63 bytes');
  }
  return value;
}

function decodeMutation(
  payload: unknown,
  expectedKind: Exclude<UbntOperationKind, 'status'>,
): UbntMutationResult {
  const response = objectValue(payload, `UBNT ${expectedKind} response`);
  if (response.ok !== true) throw new TypeError(`UBNT ${expectedKind} response.ok must be true`);
  const status = decodeUbntWifiStatus(payload);
  if (status.operation.status !== 'running' || status.operation.kind !== expectedKind) {
    throw new TypeError(`UBNT ${expectedKind} response did not start the expected operation`);
  }
  return {
    message: stringValue(response.message, `UBNT ${expectedKind} response.message`),
    status,
  };
}

export async function scanUbntNetworks(): Promise<UbntMutationResult> {
  return decodeMutation(await postForm('/api/ubnt-wifi/scan'), 'scan');
}

export async function connectUbntProfile(profile: string): Promise<UbntMutationResult> {
  const payload = await postForm('/api/ubnt-wifi/connect', {
    profile: profileName(profile),
  });
  return decodeMutation(payload, 'connect');
}

export async function provisionUbntNetwork(
  request: UbntProvisionRequest,
): Promise<UbntMutationResult> {
  let secret = request.password;
  try {
    if (
      !request.ssid ||
      utf8Length(request.ssid) > 32 ||
      request.ssid.startsWith('.') ||
      request.ssid.includes('/') ||
      hasControlCharacter(request.ssid)
    ) {
      throw new TypeError('SSID cannot be safely stored as a UBNT profile');
    }
    if (request.security === 'none' && secret) {
      throw new TypeError('Open networks do not use a password');
    }
    secret = request.security === 'wpa' ? password(secret, false) : '';

    const requestPromise = postForm('/api/ubnt-wifi/provision', {
      ssid: request.ssid,
      security: request.security,
      bssid: bssid(request.bssid, false),
      password: secret,
    });
    // postForm has already copied the value into its request body. Drop every
    // feature-owned reference before waiting for the network response.
    secret = '';
    request.password = '';
    const payload = await requestPromise;
    return decodeMutation(payload, 'provision');
  } finally {
    secret = '';
    request.password = '';
  }
}

export async function resumeUbntAutomaticSelection(): Promise<UbntMutationResult> {
  return decodeMutation(await postForm('/api/ubnt-wifi/resume'), 'resume');
}

export async function updateUbntProfile(update: UbntProfileUpdate): Promise<UbntMutationResult> {
  let secret = update.password;
  try {
    if (
      !Number.isInteger(update.outputPowerDbm) ||
      update.outputPowerDbm < 0 ||
      update.outputPowerDbm > 23
    ) {
      throw new TypeError('UBNT output power must be a whole number from 0 to 23 dBm');
    }
    if (!Number.isInteger(update.rateMcs) || update.rateMcs < 0 || update.rateMcs > 15) {
      throw new TypeError('UBNT maximum TX rate must be MCS 0 to 15');
    }
    if (update.rateModule !== 'atheros' && update.rateModule !== 'ewma_ht') {
      throw new TypeError('UBNT data-rate module is invalid');
    }
    secret = password(secret, true);

    const requestPromise = postForm('/api/ubnt-wifi/profile', {
      profile: profileName(update.profile),
      password: secret,
      bssid: bssid(update.bssid, true),
      output_power_dbm: String(update.outputPowerDbm),
      rate_module: update.rateModule,
      rate_auto: String(update.rateAuto),
      rate_mcs: String(update.rateMcs),
      apply_now: String(update.applyNow),
    });
    secret = '';
    update.password = '';
    const payload = await requestPromise;
    return decodeMutation(payload, 'update-profile');
  } finally {
    secret = '';
    update.password = '';
  }
}
