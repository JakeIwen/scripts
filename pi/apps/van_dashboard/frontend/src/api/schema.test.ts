import { describe, expect, it } from 'vitest';
import * as v from 'valibot';

import {
  booleanValue,
  decode,
  finiteNumber,
  messageFrom,
  nonnegativeInteger,
  nullableBoolean,
  nullableNumber,
  nullableOneOf,
  nullableString,
  object,
  oneOf,
  optionalNullableBoolean,
  optionalNullableNumber,
  optionalNullableString,
  optionalString,
  positiveInteger,
  stringArray,
  text,
  trueValue,
} from './schema';

const fields = {
  text,
  finiteNumber,
  booleanValue,
  trueValue,
  nonnegativeInteger,
  positiveInteger,
  nullableString,
  nullableNumber,
  nullableBoolean,
  optionalString,
  optionalNullableString,
  optionalNullableNumber,
  optionalNullableBoolean,
  stringArray,
  choice: oneOf(['known']),
  nullableChoice: nullableOneOf(['known']),
};

function error(schema: v.GenericSchema, input: unknown, message: string) {
  expect(() => decode(schema, input, 'root')).toThrow(TypeError);
  expect(() => decode(schema, input, 'root')).toThrow(message);
}

describe('shared response schemas', () => {
  it('preserves primitive wording and finite-number boundaries', () => {
    for (const input of [undefined, null, false, {}, [], 42])
      error(text, input, 'root must be text');
    for (const input of [undefined, null, false, {}, [], '2', NaN, Infinity, -Infinity]) {
      error(finiteNumber, input, 'root must be a finite number');
    }
    for (const input of [undefined, null, 0, 1, {}, [], 'true']) {
      error(booleanValue, input, 'root must be true or false');
    }
    for (const input of [undefined, null, false, 1, 'true'])
      error(trueValue, input, 'root must be true');
    error(stringArray, {}, 'root must be an array');
    error(nonnegativeInteger, -1, 'root must be a non-negative integer');
    error(nonnegativeInteger, 1.5, 'root must be a non-negative integer');
    error(nonnegativeInteger, NaN, 'root must be a finite number');
    error(positiveInteger, 0, 'root must be positive');
    error(positiveInteger, -1, 'root must be a non-negative integer');
  });

  it('keeps picklist text validation ahead of unsupported-value wording', () => {
    error(fields.choice, 42, 'root must be text');
    error(fields.choice, 'future', 'root has an unsupported value: future');
    expect(decode(fields.choice, 'known', 'root')).toBe('known');
    expect(decode(fields.nullableChoice, null, 'root')).toBeNull();
  });

  it('strips unknown keys, rejects arrays as objects, and keeps optional keys absent', () => {
    const schema = object({ name: text, note: optionalString });
    expect(decode(schema, { name: 'name', unknown: 1 }, 'root')).toStrictEqual({ name: 'name' });
    expect(decode(schema, { name: 'name', note: undefined }, 'root')).toStrictEqual({
      name: 'name',
      note: undefined,
    });
    for (const value of [null, [], 'x', 42]) error(schema, value, 'root must be an object');
    error(object({}), [], 'root must be an object');
  });

  it('reports missing fields with their child schema wording, including nested paths', () => {
    for (const field of [
      text,
      finiteNumber,
      booleanValue,
      trueValue,
      nonnegativeInteger,
      stringArray,
      fields.choice,
      v.nullable(text),
      object({ value: text }),
    ]) {
      let suffix = '';
      try {
        decode(field, undefined, 'root.value');
      } catch (caught) {
        if (!(caught instanceof TypeError)) throw caught;
        suffix = caught.message;
      }
      error(object({ value: field }), {}, suffix);
    }
    error(
      object({ items: v.array(object({ name: text })) }),
      { items: [{}] },
      'root.items[0].name must be text',
    );
    error(
      object({ items: v.array(object({ name: text })) }),
      { items: [[]] },
      'root.items[0] must be an object',
    );
  });

  it('defaults only the optional-nullable primitives and accepts explicit null', () => {
    const schema = object({
      text: optionalNullableString,
      number: optionalNullableNumber,
      bool: optionalNullableBoolean,
    });
    expect(decode(schema, {}, 'root')).toStrictEqual({ text: null, number: null, bool: null });
    expect(decode(schema, { text: null, number: null, bool: null }, 'root')).toStrictEqual({
      text: null,
      number: null,
      bool: null,
    });
    expect(decode(nullableNumber, null, 'root')).toBeNull();
    error(nullableString, undefined, 'root must be text');
  });

  it('aborts at the first field without evaluating later checks', () => {
    let checked = false;
    const schema = object({
      first: text,
      second: v.pipe(
        text,
        v.check(() => {
          checked = true;
          return false;
        }, 'must pass'),
      ),
    });
    error(schema, { first: 42, second: 'x' }, 'root.first must be text');
    expect(checked).toBe(false);
  });

  it('formats forwarded custom suffixes and extracts response messages', () => {
    const schema = v.pipe(
      object({ count: finiteNumber }),
      v.forward(
        v.check((row) => row.count === 1, 'must match one'),
        ['count'],
      ),
    );
    error(schema, { count: 2 }, 'root.count must match one');
    expect(messageFrom({ message: 'done', extra: 1 })).toBe('done');
    expect(messageFrom({})).toBeUndefined();
    expect(() => messageFrom([])).toThrow('response must be an object');
    expect(() => messageFrom({ message: null })).toThrow('response.message must be text');
  });
});
