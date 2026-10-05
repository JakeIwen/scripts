import { readFileSync } from 'node:fs';
import { inspect } from 'node:util';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

import {
  assertFrozenParity,
  captureDecode,
  freezeResults,
  parityCorpus,
  type FrozenResults,
  type ParityCase,
} from '../../test/parity';
import {
  decodeConnectivityResponse,
  decodeOpenWrtClientsResponse,
  decodeSpeedtestResponse,
  decodeStartSpeedtestResponse,
} from './schema';
import {
  clientFixtures,
  clientTargeted,
  connectivityFixtures,
  connectivityTargeted,
  speedtestFixtures,
  startFixtures,
  startTargeted,
} from './parityFixtures';
import { decodeLegacyStartSpeedtestResponse } from './legacyStartDecoder';
import { decodeConnectivityResponse as decodeLegacyConnectivityResponse } from './decoders';
import { decodeOpenWrtClientsResponse as decodeLegacyOpenWrtClientsResponse } from './decoders';
import { decodeSpeedtestResponse as decodeLegacySpeedtestResponse } from './decoders';

// Explicit per-feature message drift table. Empty means every rejected case stays verbatim.
const MESSAGE_DRIFT: Record<string, string> = {};
// No absent-versus-explicit-undefined exceptions: outputs are compared without normalization.

type Decoder = (value: unknown) => unknown;

interface ParityGroup {
  name: string;
  fixtures: Record<string, Record<string, unknown>>;
  targeted: ParityCase[];
  current: Decoder;
  legacy: Decoder;
}

const groups: ParityGroup[] = [
  {
    name: 'connectivity',
    fixtures: connectivityFixtures,
    targeted: connectivityTargeted,
    current: decodeConnectivityResponse,
    legacy: decodeLegacyConnectivityResponse,
  },
  {
    name: 'openwrt-clients',
    fixtures: clientFixtures,
    targeted: clientTargeted,
    current: decodeOpenWrtClientsResponse,
    legacy: decodeLegacyOpenWrtClientsResponse,
  },
  {
    name: 'speedtest',
    fixtures: speedtestFixtures,
    targeted: [],
    current: decodeSpeedtestResponse,
    legacy: decodeLegacySpeedtestResponse,
  },
  {
    name: 'start-speedtest',
    fixtures: startFixtures,
    targeted: startTargeted,
    current: decodeStartSpeedtestResponse,
    legacy: decodeLegacyStartSpeedtestResponse,
  },
];

function snapshotPath(name: string): string {
  return join(process.cwd(), 'src/features/network', `parity.${name}.json.snap`);
}

function casesFor(group: ParityGroup): ParityCase[] {
  return [...parityCorpus(group.fixtures), ...group.targeted];
}

describe('network generated decoder parity', () => {
  for (const group of groups) {
    it(`${group.name}: matches the legacy decoder on frozen generated inputs`, async () => {
      const cases = casesFor(group);
      const oldResults = cases.map(({ input }) => captureDecode(group.legacy, input));

      cases.forEach((parityCase, index) => {
        const oldResult = oldResults[index];
        if (!oldResult) throw new Error(`Missing legacy result for ${parityCase.name}`);
        const currentResult = captureDecode(group.current, parityCase.input);
        expect(
          currentResult,
          `${group.name} parity case ${parityCase.name}; input ${inspect(parityCase.input, { depth: null, sorted: true })}`,
        ).toStrictEqual(oldResult);
      });

      const frozen = freezeResults(cases, oldResults);
      const path = snapshotPath(group.name);
      await expect(JSON.stringify(frozen)).toMatchFileSnapshot(path);
      const fromSnapshot: FrozenResults = JSON.parse(readFileSync(path, 'utf8'));
      assertFrozenParity(cases, group.current, fromSnapshot, MESSAGE_DRIFT);
    });
  }
});
