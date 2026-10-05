import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, it } from 'vitest';

import {
  assertFrozenParity,
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

// Explicit per-feature message drift table. Empty means every rejected case stays verbatim.
const MESSAGE_DRIFT: Record<string, string> = {};
// No absent-versus-explicit-undefined exceptions: outputs are compared without normalization.

type Decoder = (value: unknown) => unknown;

interface ParityGroup {
  name: string;
  fixtures: Record<string, Record<string, unknown>>;
  targeted: ParityCase[];
  current: Decoder;
}

const groups: ParityGroup[] = [
  {
    name: 'connectivity',
    fixtures: connectivityFixtures,
    targeted: connectivityTargeted,
    current: decodeConnectivityResponse,
  },
  {
    name: 'openwrt-clients',
    fixtures: clientFixtures,
    targeted: clientTargeted,
    current: decodeOpenWrtClientsResponse,
  },
  {
    name: 'speedtest',
    fixtures: speedtestFixtures,
    targeted: [],
    current: decodeSpeedtestResponse,
  },
  {
    name: 'start-speedtest',
    fixtures: startFixtures,
    targeted: startTargeted,
    current: decodeStartSpeedtestResponse,
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
    it(`${group.name}: matches frozen expectations on generated inputs`, () => {
      const cases = casesFor(group);
      const frozen: FrozenResults = JSON.parse(readFileSync(snapshotPath(group.name), 'utf8'));
      assertFrozenParity(cases, group.current, frozen, MESSAGE_DRIFT);
    });
  }
});
