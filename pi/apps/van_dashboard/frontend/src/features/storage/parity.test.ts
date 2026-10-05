import { inspect } from 'node:util';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

import {
  assertFrozenParity,
  captureDecode,
  freezeResults,
  parityCorpus,
  type ParityCase,
} from '../../test/parity';
import {
  decodeDiskMutation,
  decodeDiskStatusResponse,
  decodeStoragePolicyMutation,
  decodeStoragePolicyResponse,
} from './schema';
import {
  decodeDiskMutationPayload,
  decodeStoragePolicyMutationPayload,
} from './legacyMutationDecoders';
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
import * as legacyDecoders from './decoders';

// Explicit per-feature message drift table. Empty means every rejected case stays verbatim.
const MESSAGE_DRIFT: Record<string, string> = {};
// No absent-versus-explicit-undefined exceptions: outputs are compared without normalization.

type Decoder = (value: unknown) => unknown;

interface ParityGroup {
  name: string;
  fixtures: Record<string, Record<string, unknown>>;
  targeted: ParityCase[];
  legacy: Decoder;
  current: Decoder;
}

const groups: ParityGroup[] = [
  {
    name: 'storage-policy',
    fixtures: storagePolicyFixtures,
    targeted: storagePolicyTargeted,
    legacy: legacyDecoders.decodeStoragePolicyResponse,
    current: decodeStoragePolicyResponse,
  },
  {
    name: 'disk-status',
    fixtures: diskStatusFixtures,
    targeted: diskStatusTargeted,
    legacy: legacyDecoders.decodeDiskStatusResponse,
    current: decodeDiskStatusResponse,
  },
  {
    name: 'storage-policy-mutation',
    fixtures: storagePolicyMutationFixtures,
    targeted: storagePolicyMutationTargeted,
    legacy: decodeStoragePolicyMutationPayload,
    current: decodeStoragePolicyMutation,
  },
  {
    name: 'disk-mutation',
    fixtures: diskMutationFixtures,
    targeted: diskMutationTargeted,
    legacy: decodeDiskMutationPayload,
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

    it(`${group.name}: new schema equals legacy on ${cases.length} generated inputs`, () => {
      for (const parityCase of cases) {
        const legacy = captureDecode(group.legacy, parityCase.input);
        const current = captureDecode(group.current, parityCase.input);
        const context = `Parity mismatch for ${group.name}/${parityCase.name}; input ${inspect(parityCase.input, { depth: null, sorted: true })}; old ${inspect(legacy, { depth: null, sorted: true })}; new ${inspect(current, { depth: null, sorted: true })}`;
        expect(current, context).toStrictEqual(legacy);
      }
    });

    it(`${group.name}: writes the compact legacy oracle`, async () => {
      const legacyResults = cases.map((parityCase) =>
        captureDecode(group.legacy, parityCase.input),
      );
      const frozen = freezeResults(cases, legacyResults);
      await expect(JSON.stringify(frozen)).toMatchFileSnapshot(snapshotPath(group.name));
    });

    it(`${group.name}: current schema matches the frozen legacy oracle`, () => {
      // Snapshot writes flush at suite end, so Stage A verifies the OLD in-memory encoding.
      const oldResults = cases.map(({ input }) => captureDecode(group.legacy, input));
      assertFrozenParity(cases, group.current, freezeResults(cases, oldResults), MESSAGE_DRIFT);
    });
  }
});
