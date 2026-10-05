import { createHash } from 'node:crypto';
import { inspect } from 'node:util';
import { expect } from 'vitest';

export interface ParityCase {
  name: string;
  input: unknown;
}

export type DecodeResult =
  { accepted: true; output: unknown } | { accepted: false; error: 'TypeError'; message: string };

type Path = (string | number)[];

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function clone(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(clone);
  if (record(value))
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, clone(v)]));
  return value;
}

function replace(value: unknown, path: Path, replacement: unknown, remove = false): unknown {
  const [key, ...rest] = path;
  if (key === undefined) return clone(replacement);
  if (Array.isArray(value) && typeof key === 'number') {
    return value.flatMap((item: unknown, index: number) =>
      index !== key
        ? [clone(item)]
        : remove && rest.length === 0
          ? []
          : [replace(item, rest, replacement, remove)],
    );
  }
  if (record(value)) {
    return Object.fromEntries(
      Object.entries(value).flatMap(([name, item]) =>
        name !== key
          ? [[name, clone(item)]]
          : remove && rest.length === 0
            ? []
            : [[name, replace(item, rest, replacement, remove)]],
      ),
    );
  }
  throw new Error(`No parity mutation path: ${path.join('.')}`);
}

/** Generated from fixtures, including every nested key and array element. Never mutates a seed. */
export function parityCorpus(fixtures: Record<string, unknown>): ParityCase[] {
  const cases: ParityCase[] = [];
  for (const [name, fixture] of Object.entries(fixtures)) {
    const add = (label: string, input: unknown) => cases.push({ name: `${name}/${label}`, input });
    add('original', clone(fixture));
    for (const [label, input] of Object.entries({ null: null, array: [], text: 'x' })) {
      add(`root:${label}`, input);
    }
    const walk = (value: unknown, path: Path, label: string) => {
      if (path.length) {
        add(`${label}:delete`, replace(fixture, path, undefined, true));
        const replacements: Record<string, unknown> = {
          null: null,
          undefined: undefined,
          text: 'x',
          number: 42,
          boolean: false,
          object: {},
          array: [],
        };
        if (typeof value === 'number') {
          Object.assign(replacements, {
            negative: -1,
            zero: 0,
            fraction: 1.5,
            nan: NaN,
            infinity: Infinity,
            negativeInfinity: -Infinity,
          });
        }
        if (typeof value === 'string') replacements.unsupported = 'future';
        if (typeof value === 'boolean') replacements.flip = !value;
        for (const [operation, replacement] of Object.entries(replacements)) {
          add(`${label}:${operation}`, replace(fixture, path, replacement));
        }
      }
      if (record(value)) {
        let extra = '__parity_extra__';
        while (extra in value) extra += '_';
        add(`${label}:extra`, replace(fixture, path, { ...value, [extra]: 'ignored' }));
        for (const [key, item] of Object.entries(value))
          walk(item, [...path, key], `${label}.${key}`);
      } else if (Array.isArray(value)) {
        value.forEach((item: unknown, index: number) =>
          walk(item, [...path, index], `${label}[${index}]`),
        );
      }
    };
    walk(fixture, [], '$');
  }
  return cases;
}

export function captureDecode(decoder: (input: unknown) => unknown, input: unknown): DecodeResult {
  try {
    return { accepted: true, output: decoder(input) };
  } catch (error) {
    if (!(error instanceof TypeError)) throw error;
    return { accepted: false, error: 'TypeError', message: error.message };
  }
}

type FrozenOutcome = string | { output: string };

export interface FrozenResults {
  namesSha256: string;
  rows: number[];
  outcomes: FrozenOutcome[];
}

function sha256(value: string): string {
  return createHash('sha256').update(value, 'utf8').digest('hex');
}

function namesHash(cases: ParityCase[]): string {
  return sha256(cases.map(({ name }) => name).join('\n'));
}

function plainRecord(value: object): value is Record<string, unknown> {
  return Object.getPrototypeOf(value) === Object.prototype;
}

function canonicalTagged(value: unknown, stack: Set<object> = new Set<object>()): string {
  if (value === undefined) return 'undefined;';
  if (value === null) return 'null;';
  switch (typeof value) {
    case 'string':
      return `string:${JSON.stringify(value)};`;
    case 'boolean':
      return `boolean:${value ? 'true' : 'false'};`;
    case 'number':
      if (Number.isNaN(value)) return 'number:NaN;';
      if (Object.is(value, -0)) return 'number:-0;';
      if (value === Infinity) return 'number:+Infinity;';
      if (value === -Infinity) return 'number:-Infinity;';
      return `number:${String(value)};`;
    case 'object': {
      if (stack.has(value)) throw new Error('Parity expectations do not support cyclic outputs');
      stack.add(value);
      try {
        if (Array.isArray(value)) {
          const keys = Reflect.ownKeys(value);
          if (
            Object.getPrototypeOf(value) !== Array.prototype ||
            keys.length !== value.length + 1 ||
            keys.some((key, index) => key !== (index < value.length ? String(index) : 'length'))
          ) {
            throw new Error('Parity expectations require dense plain JSON-like outputs');
          }
          return `array:[${value.map((item: unknown) => canonicalTagged(item, stack)).join(',')}]`;
        }
        if (!plainRecord(value) || Reflect.ownKeys(value).length !== Object.keys(value).length) {
          throw new Error('Parity expectations require plain JSON-like outputs');
        }
        const fields = Object.keys(value)
          .sort()
          .map((key) => `key:${JSON.stringify(key)}=${canonicalTagged(value[key], stack)}`)
          .join(',');
        return `object:{${fields}}`;
      } finally {
        stack.delete(value);
      }
    }
    default:
      throw new Error('Parity expectations require plain JSON-like outputs');
  }
}

function outputFingerprint(value: unknown): string {
  return sha256(canonicalTagged(value)).slice(0, 16);
}

function normalizeResult(result: DecodeResult): FrozenOutcome {
  return result.accepted ? { output: outputFingerprint(result.output) } : result.message;
}

/** Freeze TypeError messages and compact fingerprints of accepted decoder outputs. */
export function freezeResults(cases: ParityCase[], results: DecodeResult[]): FrozenResults {
  if (results.length !== cases.length) {
    throw new Error(
      `Parity result count ${results.length} does not match case count ${cases.length}`,
    );
  }
  const outcomes: FrozenOutcome[] = [];
  const ids = new Map<string, number>();
  const intern = (outcome: FrozenOutcome): number => {
    const key = JSON.stringify(outcome);
    const existing = ids.get(key);
    if (existing !== undefined) return existing;
    const id = outcomes.length;
    ids.set(key, id);
    outcomes.push(outcome);
    return id;
  };
  const rows = results.map((result, index) => {
    const parityCase = cases[index];
    if (!parityCase) throw new Error(`Missing case for parity result ${index}`);
    return intern(normalizeResult(result));
  });
  return { namesSha256: namesHash(cases), rows, outcomes };
}

export function assertFrozenParity(
  cases: ParityCase[],
  decoder: (input: unknown) => unknown,
  frozen: FrozenResults,
  drift: Record<string, string> = {},
): void {
  const recomputedNamesSha256 = namesHash(cases);
  expect(
    recomputedNamesSha256,
    `Parity names hash mismatch: recomputed ${recomputedNamesSha256}, frozen ${frozen.namesSha256}`,
  ).toBe(frozen.namesSha256);
  expect(
    frozen.rows.length,
    `Parity row count mismatch: recomputed case count ${cases.length}, frozen row count ${frozen.rows.length}`,
  ).toBe(cases.length);

  cases.forEach((parityCase, index) => {
    const row = frozen.rows[index];
    if (row === undefined) {
      throw new Error(`Parity case ${parityCase.name} has no frozen row`);
    }
    const expectedOutcome = frozen.outcomes[row];
    if (expectedOutcome === undefined) {
      throw new Error(`Parity case ${parityCase.name} references missing frozen outcome ${row}`);
    }
    const actualResult = captureDecode(decoder, parityCase.input);
    const actualOutcome = normalizeResult(actualResult);
    const expected =
      typeof expectedOutcome === 'string'
        ? (drift[expectedOutcome] ?? expectedOutcome)
        : expectedOutcome;
    const actualOutput = actualResult.accepted
      ? `; actual decoded output ${inspect(actualResult.output, { depth: null, sorted: true })}`
      : '';
    const context = `Parity mismatch for recomputed case name ${parityCase.name}; expected outcome ${inspect(expected, { depth: null, sorted: true })}; actual outcome ${inspect(actualOutcome, { depth: null, sorted: true })}${actualOutput}`;
    expect(actualOutcome, context).toStrictEqual(expected);
  });
}
