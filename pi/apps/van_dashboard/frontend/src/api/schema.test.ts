import { describe, expect, it } from 'vitest';

import {
  decode,
  messageFrom,
  nullableNumber,
  nullableString,
  object,
  optionalNullableBoolean,
  optionalNullableNumber,
  optionalNullableString,
  optionalString,
  text,
} from './schema';

describe('shared response schemas', () => {
  it('strips unknown keys, rejects arrays as objects, and keeps optional keys absent', () => {
    const schema = object({ name: text, note: optionalString });
    expect(decode(schema, { name: 'name', unknown: 1 }, 'root')).toStrictEqual({ name: 'name' });
    expect(decode(schema, { name: 'name', note: undefined }, 'root')).toStrictEqual({
      name: 'name',
      note: undefined,
    });
    for (const value of [null, [], 'x', 42]) {
      expect(() => decode(schema, value, 'root')).toThrow(TypeError);
    }
  });

  it('defaults only the optional-nullable primitives and accepts explicit null', () => {
    const schema = object({
      text: optionalNullableString,
      number: optionalNullableNumber,
      bool: optionalNullableBoolean,
    });
    expect(decode(schema, {}, 'root')).toStrictEqual({ text: null, number: null, bool: null });
    expect(decode(nullableNumber, null, 'root')).toBeNull();
    expect(() => decode(nullableString, undefined, 'root')).toThrow(TypeError);
  });

  it('extracts response messages', () => {
    expect(messageFrom({ message: 'done', extra: 1 })).toBe('done');
    expect(messageFrom({})).toBeUndefined();
    expect(() => messageFrom({ message: null })).toThrow(TypeError);
  });
});
