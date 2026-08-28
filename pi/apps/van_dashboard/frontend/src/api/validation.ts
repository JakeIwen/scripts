export type JsonObject = Record<string, unknown>;

export function objectValue(value: unknown, label: string): JsonObject {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new TypeError(`${label} must be an object`);
  }
  return value as JsonObject;
}

export function arrayValue(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) {
    throw new TypeError(`${label} must be an array`);
  }
  return value;
}

export function stringValue(value: unknown, label: string): string {
  if (typeof value !== 'string') {
    throw new TypeError(`${label} must be text`);
  }
  return value;
}

export function nullableString(value: unknown, label: string): string | null {
  return value === null ? null : stringValue(value, label);
}

export function numberValue(value: unknown, label: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new TypeError(`${label} must be a finite number`);
  }
  return value;
}

export function nullableNumber(value: unknown, label: string): number | null {
  return value === null ? null : numberValue(value, label);
}

export function booleanValue(value: unknown, label: string): boolean {
  if (typeof value !== 'boolean') {
    throw new TypeError(`${label} must be true or false`);
  }
  return value;
}

export function nullableBoolean(value: unknown, label: string): boolean | null {
  return value === null ? null : booleanValue(value, label);
}

export function optionalString(value: unknown, label: string): string | undefined {
  return value === undefined ? undefined : stringValue(value, label);
}

export function messageFrom(value: unknown): string | undefined {
  const object = objectValue(value, 'response');
  return optionalString(object.message, 'response.message');
}
