import { describe, expect, it } from 'vitest';

import {
  assertFrozenParity,
  captureDecode,
  freezeResults,
  parityCorpus,
  type DecodeResult,
} from './parity';

describe('generated decoder parity corpus', () => {
  it('walks every object key and array element without modifying fixtures', () => {
    const fixture = { items: [{ name: 'name', count: 1, enabled: true }], missing: undefined };
    const before = structuredClone(fixture);
    const cases = parityCorpus({ seed: fixture });
    expect(new Set(cases.map((row) => row.name)).size).toBe(cases.length);
    expect(cases.find((row) => row.name === 'seed/$.items[0].name:delete')?.input).toStrictEqual({
      items: [{ count: 1, enabled: true }],
      missing: undefined,
    });
    expect(cases.find((row) => row.name === 'seed/$.items[0]:delete')?.input).toStrictEqual({
      items: [],
      missing: undefined,
    });
    expect(cases.find((row) => row.name === 'seed/$.items[0]:extra')?.input).toStrictEqual({
      items: [{ name: 'name', count: 1, enabled: true, __parity_extra__: 'ignored' }],
      missing: undefined,
    });
    expect(fixture).toStrictEqual(before);
    expect(cases[0]?.input).not.toBe(fixture);
    expect(parityCorpus({ seed: fixture })).toStrictEqual(cases);
  });

  it('includes root replacements, wrong types, missing, unknown keys and numeric boundaries', () => {
    const cases = parityCorpus({ seed: { count: 1 } });
    expect(cases.slice(0, 4).map((row) => row.input)).toStrictEqual([{ count: 1 }, null, [], 'x']);
    for (const operation of [
      'delete',
      'null',
      'undefined',
      'text',
      'number',
      'boolean',
      'object',
      'array',
      'negative',
      'zero',
      'fraction',
      'nan',
      'infinity',
      'negativeInfinity',
    ]) {
      expect(cases.some((row) => row.name === `seed/$.count:${operation}`)).toBe(true);
    }
    expect(cases.find((row) => row.name === 'seed/$.count:nan')?.input).toStrictEqual({
      count: NaN,
    });
    expect(cases.some((row) => row.name === 'seed/$:extra')).toBe(true);
    expect(
      parityCorpus({ seed: { __parity_extra__: 1 } }).find((row) => row.name === 'seed/$:extra')
        ?.input,
    ).toStrictEqual({ __parity_extra__: 1, __parity_extra___: 'ignored' });
  });

  it('captures only TypeError failures and keeps output identity intact', () => {
    const output = { key: undefined };
    expect(captureDecode(() => output, null)).toStrictEqual({ accepted: true, output });
    expect(
      captureDecode(() => {
        throw new TypeError('first field');
      }, null),
    ).toStrictEqual({ accepted: false, error: 'TypeError', message: 'first field' });
    expect(() =>
      captureDecode(() => {
        throw new Error('bug');
      }, null),
    ).toThrow('bug');
  });

  it('fingerprints canonical outputs and interns compact repeated outcomes', () => {
    const accepted = (output: unknown): DecodeResult => ({ accepted: true, output });
    const cases = [
      { name: 'absent', input: null },
      { name: 'undefined', input: null },
      { name: 'null', input: null },
      { name: 'zero', input: null },
      { name: 'negative-zero', input: null },
      { name: 'nan', input: null },
      { name: 'infinity', input: null },
      { name: 'negative-infinity', input: null },
      { name: 'sorted-a', input: null },
      { name: 'sorted-b', input: null },
      { name: 'array-forward', input: null },
      { name: 'array-reverse', input: null },
      { name: 'repeated', input: null },
    ];
    const packed = freezeResults(cases, [
      accepted({}),
      accepted({ value: undefined }),
      accepted(null),
      accepted(0),
      accepted(-0),
      accepted(NaN),
      accepted(Infinity),
      accepted(-Infinity),
      accepted({ a: 1, b: 2 }),
      accepted({ b: 2, a: 1 }),
      accepted([1, 2]),
      accepted([2, 1]),
      accepted({}),
    ]);

    expect(packed.rows[0]).not.toBe(packed.rows[1]);
    expect(packed.rows[0]).not.toBe(packed.rows[2]);
    expect(packed.rows[3]).not.toBe(packed.rows[4]);
    expect(packed.rows[5]).not.toBe(packed.rows[6]);
    expect(packed.rows[6]).not.toBe(packed.rows[7]);
    expect(packed.rows[8]).toBe(packed.rows[9]);
    expect(packed.rows[10]).not.toBe(packed.rows[11]);
    expect(packed.rows[0]).toBe(packed.rows[12]);
    expect(packed.outcomes).toHaveLength(11);
    expect(packed.namesSha256).toMatch(/^[0-9a-f]{64}$/);
  });

  it('rejects unsupported output prototypes and types', () => {
    const accepted = (output: unknown): DecodeResult => ({ accepted: true, output });
    expect(() => freezeResults([{ name: 'date', input: null }], [accepted(new Date(0))])).toThrow(
      'plain JSON-like outputs',
    );
    expect(() => freezeResults([{ name: 'bigint', input: null }], [accepted(BigInt(1))])).toThrow(
      'plain JSON-like outputs',
    );
    const sparse: unknown[] = [];
    sparse.length = 1;
    for (const output of [
      sparse,
      Object.assign([], { extra: true }),
      { [Symbol('hidden')]: true },
      Object.defineProperty({}, 'hidden', { value: true }),
    ]) {
      expect(() =>
        freezeResults([{ name: 'unsupported', input: null }], [accepted(output)]),
      ).toThrow('plain JSON-like outputs');
    }
  });

  it('guards rejected outcomes and includes decoded output in mismatch diagnostics', () => {
    const cases = [{ name: 'rejection', input: null }];
    const frozen = freezeResults(cases, [
      { accepted: false, error: 'TypeError', message: 'legacy rejection' },
    ]);
    expect(() => assertFrozenParity(cases, () => ({ actual: true }), frozen)).toThrow(
      /recomputed case name rejection.*expected outcome.*actual outcome.*actual decoded output/,
    );
  });

  it('reports names and row-count drift before comparing rows', () => {
    const oneCase = [{ name: 'one', input: null }];
    const frozen = freezeResults(oneCase, [{ accepted: true, output: null }]);
    expect(() =>
      assertFrozenParity([{ name: 'renamed', input: null }], () => null, frozen),
    ).toThrow(/names hash mismatch/);

    const twoCases = [
      { name: 'one', input: null },
      { name: 'two', input: null },
    ];
    const twoFrozen = freezeResults(twoCases, [
      { accepted: true, output: null },
      { accepted: true, output: null },
    ]);
    const shortFrozen = { ...twoFrozen, rows: twoFrozen.rows.slice(0, 1) };
    expect(() => assertFrozenParity(twoCases, () => null, shortFrozen)).toThrow(
      /row count mismatch/,
    );
  });
});
