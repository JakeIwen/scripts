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

type FrozenValue =
  | ['value', string | number | boolean | null]
  | ['undefined']
  | ['number', string]
  | ['array', number[]]
  | ['object', [string, number][]];

export interface FrozenResults {
  rows: [string, number][];
  values: FrozenValue[];
}

/** Intern repeated subtrees: a one-field mutation need not freeze the entire payload again.
 * Tagged values preserve undefined, negative zero, and non-finite numbers through JSON.
 */
export function freezeResults(cases: ParityCase[], results: DecodeResult[]): FrozenResults {
  const values: FrozenValue[] = [];
  const ids = new Map<string, number>();
  const intern = (value: unknown): number => {
    let node: FrozenValue;
    if (value === undefined) node = ['undefined'];
    else if (typeof value === 'number' && (!Number.isFinite(value) || Object.is(value, -0))) {
      node = ['number', Object.is(value, -0) ? '-0' : String(value)];
    } else if (
      value === null ||
      typeof value === 'string' ||
      typeof value === 'number' ||
      typeof value === 'boolean'
    ) {
      node = ['value', value];
    } else if (Array.isArray(value)) node = ['array', value.map(intern)];
    else if (record(value) && Object.getPrototypeOf(value) === Object.prototype) {
      node = [
        'object',
        Object.entries(value).map(([key, item]): [string, number] => [key, intern(item)]),
      ];
    } else throw new Error('Parity expectations require plain JSON-like outputs');
    const key = JSON.stringify(node);
    let id = ids.get(key);
    if (id === undefined) {
      id = values.length;
      ids.set(key, id);
      values.push(node);
    }
    return id;
  };
  const rows: FrozenResults['rows'] = cases.map(({ name }, index) => {
    const result = results[index];
    if (!result) throw new Error(`Missing result for ${name}`);
    return [name, intern(result)];
  });
  return { rows, values };
}

export function thawResults(frozen: FrozenResults): { name: string; result: unknown }[] {
  const expand = (id: number): unknown => {
    const node = frozen.values[id];
    if (!node) throw new Error(`Missing frozen node ${id}`);
    switch (node[0]) {
      case 'value':
        return node[1];
      case 'undefined':
        return undefined;
      case 'number':
        return Number(node[1]);
      case 'array':
        return node[1].map(expand);
      case 'object':
        return Object.fromEntries(node[1].map(([key, child]) => [key, expand(child)]));
    }
  };
  return frozen.rows.map(([name, id]) => ({ name, result: expand(id) }));
}
