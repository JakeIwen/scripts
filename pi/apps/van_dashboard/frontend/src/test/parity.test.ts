import { describe, expect, it } from 'vitest';

import { captureDecode, freezeResults, parityCorpus, thawResults } from './parity';

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

  it('interns only strictly equal results, retaining explicit undefined and nonfinite values', () => {
    const outputs = [
      {},
      { value: undefined },
      { value: null },
      { value: NaN },
      { value: Infinity },
      {},
    ];
    const cases = outputs.map((input, index) => ({ name: String(index), input }));
    const packed = freezeResults(
      cases,
      outputs.map((output) => ({ accepted: true, output })),
    );
    expect(packed.rows[0]?.[1]).toBe(packed.rows[5]?.[1]);
    expect(new Set(packed.rows.map((row) => row[1])).size).toBe(5);
    expect(thawResults(JSON.parse(JSON.stringify(packed)))).toStrictEqual(
      outputs.map((output, index) => ({ name: String(index), result: { accepted: true, output } })),
    );
  });
});
