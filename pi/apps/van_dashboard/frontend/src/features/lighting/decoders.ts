import {
  arrayValue,
  booleanValue,
  nullableNumber,
  nullableString,
  objectValue,
  stringValue,
} from '../../api/validation';
import type {
  LightDeviceState,
  LightingAggregateState,
  LightingGroup,
  LightingLight,
  LightingPowerSwitch,
  LightingStatus,
} from './types';

const AGGREGATE_STATES = [
  'on',
  'off',
  'mixed',
  'unknown',
] as const satisfies readonly LightingAggregateState[];
const DEVICE_STATES = [
  'on',
  'off',
  'unavailable',
  'unknown',
] as const satisfies readonly LightDeviceState[];

function trueValue(value: unknown, label: string): true {
  if (value !== true) throw new TypeError(`${label} must be true`);
  return true;
}

function nonemptyString(value: unknown, label: string): string {
  const text = stringValue(value, label);
  if (!text.trim()) throw new TypeError(`${label} must not be empty`);
  return text;
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[], label: string): T {
  const text = stringValue(value, label);
  if (!allowed.includes(text as T)) {
    throw new TypeError(`${label} has an unsupported value: ${text}`);
  }
  return text as T;
}

function nonnegativeInteger(value: unknown, label: string): number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 0) {
    throw new TypeError(`${label} must be a non-negative whole number`);
  }
  return value;
}

function nullableIntegerInRange(
  value: unknown,
  minimum: number,
  maximum: number,
  label: string,
): number | null {
  const number = nullableNumber(value, label);
  if (number !== null && (!Number.isInteger(number) || number < minimum || number > maximum)) {
    throw new TypeError(`${label} must be a whole number from ${minimum} to ${maximum}`);
  }
  return number;
}

function nullableNumberInRange(
  value: unknown,
  minimum: number,
  maximum: number,
  label: string,
): number | null {
  const number = nullableNumber(value, label);
  if (number !== null && (number < minimum || number > maximum)) {
    throw new TypeError(`${label} must be from ${minimum} to ${maximum}`);
  }
  return number;
}

function entityId(value: unknown, label: string): string {
  const entity = nonemptyString(value, label);
  if (!/^(?:light|switch)\.[a-z0-9_]+$/.test(entity)) {
    throw new TypeError(`${label} must be a lighting entity ID`);
  }
  return entity;
}

function colorMode(value: unknown, label: string): string | null {
  const mode = nullableString(value, label);
  if (mode !== null && !/^[a-z0-9_]{1,32}$/.test(mode)) {
    throw new TypeError(`${label} contains an invalid color mode`);
  }
  return mode;
}

function aggregate(lights: readonly LightingLight[]): LightingAggregateState {
  const states = lights.map((light) => light.state);
  if (states.length > 0 && states.every((state) => state === 'on')) return 'on';
  if (states.length > 0 && states.every((state) => state === 'off')) return 'off';
  if (states.some((state) => state === 'on' || state === 'off')) return 'mixed';
  return 'unknown';
}

function decodeLight(value: unknown, groupIndex: number, lightIndex: number): LightingLight {
  const label = `lighting.groups[${groupIndex}].lights[${lightIndex}]`;
  const object = objectValue(value, label);
  const state = oneOf(object.state, DEVICE_STATES, `${label}.state`);
  const available = booleanValue(object.available, `${label}.available`);
  if (available !== (state === 'on' || state === 'off')) {
    throw new TypeError(`${label}.available does not match ${label}.state`);
  }

  const supportsHue = booleanValue(object.supports_hue, `${label}.supports_hue`);
  const hue = nullableNumberInRange(object.hue, 0, 360, `${label}.hue`);
  if (!supportsHue && hue !== null) {
    throw new TypeError(`${label}.hue must be null when hue is unsupported`);
  }

  const supportsColorTemperature = booleanValue(
    object.supports_color_temperature,
    `${label}.supports_color_temperature`,
  );
  const colorTemperatureKelvin = nullableIntegerInRange(
    object.color_temp_kelvin,
    1_000,
    10_000,
    `${label}.color_temp_kelvin`,
  );
  const minimumColorTemperatureKelvin = nullableIntegerInRange(
    object.min_color_temp_kelvin,
    1_000,
    10_000,
    `${label}.min_color_temp_kelvin`,
  );
  const maximumColorTemperatureKelvin = nullableIntegerInRange(
    object.max_color_temp_kelvin,
    1_000,
    10_000,
    `${label}.max_color_temp_kelvin`,
  );

  if (
    supportsColorTemperature &&
    (minimumColorTemperatureKelvin === null || maximumColorTemperatureKelvin === null)
  ) {
    throw new TypeError(`${label} must include its supported color-temperature range`);
  }
  if (
    minimumColorTemperatureKelvin !== null &&
    maximumColorTemperatureKelvin !== null &&
    minimumColorTemperatureKelvin > maximumColorTemperatureKelvin
  ) {
    throw new TypeError(`${label} has an inverted color-temperature range`);
  }
  if (
    colorTemperatureKelvin !== null &&
    ((minimumColorTemperatureKelvin !== null &&
      colorTemperatureKelvin < minimumColorTemperatureKelvin) ||
      (maximumColorTemperatureKelvin !== null &&
        colorTemperatureKelvin > maximumColorTemperatureKelvin))
  ) {
    throw new TypeError(`${label}.color_temp_kelvin is outside its supported range`);
  }

  return {
    entityId: entityId(object.entity_id, `${label}.entity_id`),
    label: nonemptyString(object.label, `${label}.label`),
    state,
    available,
    brightness: nullableIntegerInRange(object.brightness, 0, 100, `${label}.brightness`),
    colorMode: colorMode(object.color_mode, `${label}.color_mode`),
    supportsHue,
    hue,
    supportsColorTemperature,
    colorTemperatureKelvin,
    minimumColorTemperatureKelvin,
    maximumColorTemperatureKelvin,
  };
}

function decodePowerSwitch(value: unknown, groupIndex: number): LightingPowerSwitch | null {
  if (value === null) return null;
  const label = `lighting.groups[${groupIndex}].power_switch`;
  const object = objectValue(value, label);
  const state = oneOf(object.state, DEVICE_STATES, `${label}.state`);
  const available = booleanValue(object.available, `${label}.available`);
  if (available !== (state === 'on' || state === 'off')) {
    throw new TypeError(`${label}.available does not match ${label}.state`);
  }
  return {
    entityId: entityId(object.entity_id, `${label}.entity_id`),
    label: nonemptyString(object.label, `${label}.label`),
    state,
    available,
  };
}

function decodeGroup(value: unknown, index: number): LightingGroup {
  const label = `lighting.groups[${index}]`;
  const object = objectValue(value, label);
  const lights = arrayValue(object.lights, `${label}.lights`).map((light, lightIndex) =>
    decodeLight(light, index, lightIndex),
  );
  if (lights.length === 0) throw new TypeError(`${label}.lights must not be empty`);

  const state = oneOf(object.state, AGGREGATE_STATES, `${label}.state`);
  if (state !== aggregate(lights)) {
    throw new TypeError(`${label}.state does not match its lights`);
  }

  return {
    id: nonemptyString(object.id, `${label}.id`),
    label: nonemptyString(object.label, `${label}.label`),
    state,
    lights,
    powerSwitch: decodePowerSwitch(object.power_switch, index),
  };
}

/** Decode and cross-check GET /api/lights before rendering device state. */
export function decodeLightingStatus(payload: unknown): LightingStatus {
  const response = objectValue(payload, 'lighting response');
  trueValue(response.ok, 'lighting response.ok');
  const lighting = objectValue(response.lighting, 'lighting');
  const groups = arrayValue(lighting.groups, 'lighting.groups').map(decodeGroup);
  const lights = groups.flatMap((group) => group.lights);

  const groupIds = new Set(groups.map((group) => group.id));
  if (groupIds.size !== groups.length) {
    throw new TypeError('lighting.groups contains duplicate IDs');
  }
  const entityIds = new Set(lights.map((light) => light.entityId));
  if (entityIds.size !== lights.length) {
    throw new TypeError('lighting.groups contains duplicate light entities');
  }

  const state = oneOf(lighting.state, AGGREGATE_STATES, 'lighting.state');
  const onCount = nonnegativeInteger(lighting.on_count, 'lighting.on_count');
  const availableCount = nonnegativeInteger(lighting.available_count, 'lighting.available_count');
  const totalCount = nonnegativeInteger(lighting.total_count, 'lighting.total_count');

  if (totalCount !== lights.length) {
    throw new TypeError('lighting.total_count does not match returned lights');
  }
  if (onCount !== lights.filter((light) => light.state === 'on').length) {
    throw new TypeError('lighting.on_count does not match returned lights');
  }
  if (availableCount !== lights.filter((light) => light.available).length) {
    throw new TypeError('lighting.available_count does not match returned lights');
  }
  if (state !== aggregate(lights)) {
    throw new TypeError('lighting.state does not match returned lights');
  }

  return { state, onCount, availableCount, totalCount, groups };
}
