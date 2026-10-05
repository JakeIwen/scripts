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
  decodeDiskMutation,
  decodeDiskStatusResponse,
  decodeStoragePolicyMutation,
  decodeStoragePolicyResponse,
} from './schema';
import {
  diskMutationFixtures,
  diskMutationTargeted,
  diskStatusFixtures,
  diskStatusTargeted,
  storagePolicyFixtures,
  storagePolicyMutationFixtures,
  storagePolicyMutationTargeted,
  storagePolicyTargeted,
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
    name: 'storage-policy',
    fixtures: storagePolicyFixtures,
    targeted: storagePolicyTargeted,
    current: decodeStoragePolicyResponse,
  },
  {
    name: 'disk-status',
    fixtures: diskStatusFixtures,
    targeted: diskStatusTargeted,
    current: decodeDiskStatusResponse,
  },
  {
    name: 'storage-policy-mutation',
    fixtures: storagePolicyMutationFixtures,
    targeted: storagePolicyMutationTargeted,
    current: decodeStoragePolicyMutation,
  },
  {
    name: 'disk-mutation',
    fixtures: diskMutationFixtures,
    targeted: diskMutationTargeted,
    current: decodeDiskMutation,
  },
];

function casesFor(group: ParityGroup): ParityCase[] {
  return [...parityCorpus(group.fixtures), ...group.targeted];
}

function snapshotPath(name: string): string {
  return join(process.cwd(), 'src/features/storage', `parity.${name}.json.snap`);
}

describe('storage generated decoder parity', () => {
  for (const group of groups) {
    const cases = casesFor(group);

    it(`${group.name}: matches the frozen legacy oracle on ${cases.length} generated inputs`, () => {
      const frozen: FrozenResults = JSON.parse(readFileSync(snapshotPath(group.name), 'utf8'));
      assertFrozenParity(cases, group.current, frozen, MESSAGE_DRIFT);
    });
  }
});
