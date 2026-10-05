import { describe, expect, it } from 'vitest';

import { objectValue } from '../../api/validation';
import { captureDecode, freezeResults, parityCorpus, thawResults } from '../../test/parity';
import * as legacy from './decoders';
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
    old: legacy.decodeUsbStatusResponse,
    current: decodeUsbStatusResponse,
  },
  {
    name: 'ports',
    fixtures: portFixtures,
    old: legacy.decodeUsbPortState,
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
    old: (value: unknown) => legacy.decodeMutation(value, label),
    current: (value: unknown) => decodeMutation(value, label),
  })),
];

describe('USB generated decoder parity', () => {
  for (const group of groups) {
    const corpus = parityCorpus(group.fixtures);
    it(`${group.name}: matches frozen expectations on ${corpus.length} generated inputs`, async () => {
      const results = corpus.map(({ name, input }) => {
        const old = captureDecode(group.old, input);
        const current = captureDecode(group.current, input);
        const expected = old.accepted
          ? old
          : { ...old, message: MESSAGE_DRIFT[old.message] ?? old.message };
        expect(current, name).toStrictEqual(expected);
        return old;
      });
      const frozen = freezeResults(corpus, results);
      expect(thawResults(frozen)).toStrictEqual(
        corpus.map(({ name }, index) => ({ name, result: results[index] })),
      );
      await expect(JSON.stringify(frozen)).toMatchFileSnapshot(
        `./parity.${group.name.replaceAll(' ', '-')}.json.snap`,
      );
    });
  }

  it('preserves the cross-field preflight ahead of metadata and ports on multiply-invalid input', () => {
    const payload = usbStatusPayload();
    const inventory = objectValue(payload.usb, 'fixture');
    inventory.present_device_count = 2;
    inventory.checked_at = 'invalid';
    payload.usb_ports = null;
    expect(captureDecode(decodeUsbStatusResponse, payload)).toStrictEqual(
      captureDecode(legacy.decodeUsbStatusResponse, payload),
    );
    expect(() => decodeUsbStatusResponse(payload)).toThrow(
      'usb.present_device_count does not match usb.devices',
    );
    inventory.devices = null;
    expect(() => decodeUsbStatusResponse(payload)).toThrow('usb.devices must be an array');
    payload.ok = false;
    expect(() => decodeUsbStatusResponse(payload)).toThrow('USB status response.ok must be true');
  });
});
