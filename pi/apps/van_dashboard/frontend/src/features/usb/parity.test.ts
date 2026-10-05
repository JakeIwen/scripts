import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

import { objectValue } from '../../api/validation';
import {
  assertFrozenParity,
  captureDecode,
  parityCorpus,
  type FrozenResults,
} from '../../test/parity';
import { decodeMutation, decodeUsbPortState, decodeUsbStatusResponse } from './schema';
import { usbStatusPayload } from './testFixtures';

// Explicit per-feature message drift table. Empty means every rejected case stays verbatim.
const MESSAGE_DRIFT: Record<string, string> = {};
// No absent-versus-explicit-undefined exceptions: outputs are compared without normalization.

const status = usbStatusPayload();
const mismatched = usbStatusPayload();
objectValue(mismatched.usb, 'fixture').present_device_count = 2;
const populated = usbStatusPayload();
objectValue(populated.usb_ports, 'fixture').operation = {
  status: 'complete',
  key: '2-2:1',
  action: 'cycle',
  started_at: 1_700_000_010,
  completed_at: 1_700_000_020,
  message: 'Port cycled',
  error: null,
};
const statusFixtures = { status, mismatched, populated };
const portFixtures = { idle: status.usb_ports, populated: populated.usb_ports };

// Includes every USB response/message fixture in storage/mutationEndpoints.test.ts.
const mutationFixtures = Object.fromEntries(
  [
    'controls loaded',
    'port enabled',
    'port disabled',
    'port cycling',
    'USB 2 recovery started',
  ].map((message) => [message, { ok: true, message, usb_ports: status.usb_ports }]),
);
const groups = [
  {
    name: 'status',
    fixtures: statusFixtures,
    current: decodeUsbStatusResponse,
  },
  {
    name: 'ports',
    fixtures: portFixtures,
    current: decodeUsbPortState,
  },
  ...[
    { label: 'USB discovery response', messages: ['controls loaded'] },
    {
      label: 'USB port-action response',
      messages: ['port enabled', 'port disabled', 'port cycling'],
    },
    { label: 'USB recovery response', messages: ['USB 2 recovery started'] },
  ].map(({ label, messages }) => ({
    name: label,
    fixtures: Object.fromEntries(messages.map((message) => [message, mutationFixtures[message]])),
    current: (value: unknown) => decodeMutation(value, label),
  })),
];

describe('USB generated decoder parity', () => {
  for (const group of groups) {
    const corpus = parityCorpus(group.fixtures);
    it(`${group.name}: matches frozen expectations on ${corpus.length} generated inputs`, () => {
      // Frozen from the legacy decoder while it and the schema passed toStrictEqual.
      // Deliberately read-only: snapshot-update flags cannot silently rewrite the oracle.
      const path = join(
        process.cwd(),
        'src/features/usb',
        `parity.${group.name.replaceAll(' ', '-')}.json.snap`,
      );
      const frozen: FrozenResults = JSON.parse(readFileSync(path, 'utf8'));
      assertFrozenParity(corpus, group.current, frozen, MESSAGE_DRIFT);
    });
  }

  it('preserves the cross-field preflight ahead of metadata and ports on multiply-invalid input', () => {
    const payload = usbStatusPayload();
    const inventory = objectValue(payload.usb, 'fixture');
    inventory.present_device_count = 2;
    inventory.checked_at = 'invalid';
    payload.usb_ports = null;
    expect(captureDecode(decodeUsbStatusResponse, payload)).toStrictEqual({
      accepted: false,
      error: 'TypeError',
      message: 'usb.present_device_count does not match usb.devices',
    });
    expect(() => decodeUsbStatusResponse(payload)).toThrow(
      'usb.present_device_count does not match usb.devices',
    );
    inventory.devices = null;
    expect(() => decodeUsbStatusResponse(payload)).toThrow('usb.devices must be an array');
    payload.ok = false;
    expect(() => decodeUsbStatusResponse(payload)).toThrow('USB status response.ok must be true');
  });
});
