import * as v from 'valibot';

export const text = v.string();
export const finiteNumber = v.pipe(v.number(), v.finite());
export const booleanValue = v.boolean();
export const trueValue = v.literal(true);
export const nullableString = v.nullable(text);
export const nullableNumber = v.nullable(finiteNumber);
export const nullableBoolean = v.nullable(booleanValue);
export const optionalString = v.optional(text);
export const optionalNullableString = v.optional(nullableString, null);
export const optionalNullableNumber = v.optional(nullableNumber, null);
export const optionalNullableBoolean = v.optional(nullableBoolean, null);
export const nonnegativeInteger = v.pipe(
  finiteNumber,
  v.check((value) => Number.isInteger(value) && value >= 0, 'must be a non-negative integer'),
);
export const positiveInteger = v.pipe(
  nonnegativeInteger,
  v.check((value) => value > 0, 'must be positive'),
);
export const stringArray = v.array(text);

export function oneOf<const T extends readonly string[]>(choices: T) {
  return v.picklist(choices);
}

export function nullableOneOf<const T extends readonly string[]>(choices: T) {
  return v.nullable(oneOf(choices));
}

/** v.object strips unknown keys, but accepts arrays and reports missing keys itself.
 * Keep the legacy object boundary and let the missing field's schema supply its wording.
 * Optional fields are not materialized, so absent and explicit undefined stay distinct.
 */
export function object<const T extends v.ObjectEntries>(entries: T) {
  return v.pipe(
    v.unknown(),
    v.check((input) => !Array.isArray(input), 'must be an object'),
    v.object(entries, (issue) => {
      const missing = issue.path?.at(-1);
      if (missing?.origin === 'key') {
        const entry = entries[String(missing.key)];
        if (entry) {
          try {
            decode(entry, undefined, '');
          } catch (error) {
            if (error instanceof TypeError) return error.message.trimStart();
            throw error;
          }
        }
      }
      return 'must be an object';
    }),
  );
}

function issueSuffix(issue: v.BaseIssue<unknown>): string {
  switch (issue.type) {
    case 'string':
      return 'must be text';
    case 'number':
    case 'finite':
      return 'must be a finite number';
    case 'boolean':
      return 'must be true or false';
    case 'object':
      return issue.message;
    case 'array':
      return 'must be an array';
    case 'literal':
      return 'must be true';
    case 'picklist':
      return typeof issue.input === 'string'
        ? `has an unsupported value: ${issue.input}`
        : 'must be text';
    // Checks carry a suffix only; forward() supplies any cross-field path.
    default:
      return issue.message;
  }
}

/** The only parsing boundary: fail fast, keep wire paths, and always throw TypeError. */
export function decode<T extends v.GenericSchema>(
  schema: T,
  input: unknown,
  rootLabel: string,
): v.InferOutput<T> {
  const result = v.safeParse(schema, input, { abortEarly: true });
  if (result.success) return result.output;
  const issue = result.issues[0];
  const path = (issue.path ?? [])
    .map((item) => (item.type === 'array' ? `[${String(item.key)}]` : `.${String(item.key)}`))
    .join('');
  throw new TypeError(`${rootLabel}${path} ${issueSuffix(issue)}`);
}

const messageSchema = object({ message: optionalString });

export function messageFrom(value: unknown): string | undefined {
  return decode(messageSchema, value, 'response').message;
}
