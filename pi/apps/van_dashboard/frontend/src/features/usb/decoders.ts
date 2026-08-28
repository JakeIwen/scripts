import {
  arrayValue,
  booleanValue,
  nullableBoolean,
  nullableNumber,
  nullableString,
  numberValue,
  objectValue,
  stringValue,
} from '../../api/validation';
import type {
  UsbDevice,
  UsbDeviceEvent,
  UsbDeviceEventKind,
  UsbDeviceStatus,
  UsbHub,
  UsbInventory,
  UsbPort,
  UsbPortAction,
  UsbPortMethod,
  UsbPortOperation,
  UsbPortOperationStatus,
  UsbPortState,
  UsbStatus,
  UsbTopologyInstance,
} from './types';

function trueValue(value: unknown, label: string): true {
  if (value !== true) throw new TypeError(`${label} must be true`);
  return true;
}

function oneOf<T extends string>(value: unknown, choices: readonly T[], label: string): T {
  const text = stringValue(value, label);
  if (!choices.includes(text as T)) {
    throw new TypeError(`${label} has an unsupported value: ${text}`);
  }
  return text as T;
}

function optionalNullableString(value: unknown, label: string): string | null {
  return value === undefined ? null : nullableString(value, label);
}

function optionalNullableNumber(value: unknown, label: string): number | null {
  return value === undefined ? null : nullableNumber(value, label);
}

function nonnegativeInteger(value: unknown, label: string): number {
  const number = numberValue(value, label);
  if (!Number.isInteger(number) || number < 0) {
    throw new TypeError(`${label} must be a non-negative integer`);
  }
  return number;
}

function positiveInteger(value: unknown, label: string): number {
  const number = nonnegativeInteger(value, label);
  if (number === 0) throw new TypeError(`${label} must be positive`);
  return number;
}

function stringArray(value: unknown, label: string): string[] {
  return arrayValue(value, label).map((item, index) => stringValue(item, `${label}[${index}]`));
}

function decodeTopologyInstance(value: unknown, label: string): UsbTopologyInstance {
  const object = objectValue(value, label);
  return {
    deviceNumber: positiveInteger(object.device_number, `${label}.device_number`),
    location: stringValue(object.location, `${label}.location`),
    parentLocation: stringValue(object.parent_location, `${label}.parent_location`),
    port: positiveInteger(object.port, `${label}.port`),
    labels: stringArray(object.labels, `${label}.labels`),
  };
}

const EVENT_KINDS = [
  'plugged',
  'replugged',
  'unplugged',
] as const satisfies readonly UsbDeviceEventKind[];

function decodeDeviceEvent(value: unknown, label: string): UsbDeviceEvent | null {
  if (value === null) return null;
  const object = objectValue(value, label);
  return {
    kind: oneOf(object.kind, EVENT_KINDS, `${label}.kind`),
    at: nonnegativeInteger(object.at, `${label}.at`),
  };
}

const DEVICE_STATUSES = [
  'unplugged',
  'partial',
  'root',
  'present',
] as const satisfies readonly UsbDeviceStatus[];

function decodeDevice(value: unknown, index: number): UsbDevice {
  const label = `usb.devices[${index}]`;
  const object = objectValue(value, label);
  return {
    bus: stringValue(object.bus, `${label}.bus`),
    deviceId: stringValue(object.device_id, `${label}.device_id`),
    description: stringValue(object.description, `${label}.description`),
    presentCount: nonnegativeInteger(object.present_count, `${label}.present_count`),
    labels: stringArray(object.labels, `${label}.labels`),
    rootHub: booleanValue(object.root_hub, `${label}.root_hub`),
    instances: arrayValue(object.instances, `${label}.instances`).map((item, itemIndex) =>
      decodeTopologyInstance(item, `${label}.instances[${itemIndex}]`),
    ),
    maxCount: positiveInteger(object.max_count, `${label}.max_count`),
    knownInstances: arrayValue(object.known_instances, `${label}.known_instances`).map(
      (item, itemIndex) => decodeTopologyInstance(item, `${label}.known_instances[${itemIndex}]`),
    ),
    event: decodeDeviceEvent(object.event, `${label}.event`),
    status: oneOf(object.status, DEVICE_STATUSES, `${label}.status`),
  };
}

function decodeInventory(value: unknown): UsbInventory {
  const object = objectValue(value, 'usb');
  const devices = arrayValue(object.devices, 'usb.devices').map(decodeDevice);
  const physicalDevices = devices.filter((device) => !device.rootHub);
  const observedPresent = physicalDevices.reduce((count, device) => count + device.presentCount, 0);
  const presentDeviceCount = nonnegativeInteger(
    object.present_device_count,
    'usb.present_device_count',
  );
  if (presentDeviceCount !== observedPresent) {
    throw new TypeError('usb.present_device_count does not match usb.devices');
  }

  return {
    checkedAt: nullableNumber(object.checked_at, 'usb.checked_at'),
    lastSuccessAt: nullableNumber(object.last_success_at, 'usb.last_success_at'),
    lastError: nullableString(object.last_error, 'usb.last_error'),
    presentDeviceCount,
    unpluggedDeviceCount: nonnegativeInteger(
      object.unplugged_device_count,
      'usb.unplugged_device_count',
    ),
    storageLabels: stringArray(object.storage_labels, 'usb.storage_labels'),
    devices,
  };
}

const PORT_METHODS = ['power', 'disable'] as const satisfies readonly UsbPortMethod[];

function decodePort(value: unknown, label: string): UsbPort {
  const object = objectValue(value, label);
  return {
    key: stringValue(object.key, `${label}.key`),
    location: stringValue(object.location, `${label}.location`),
    port: positiveInteger(object.port, `${label}.port`),
    method: oneOf(object.method, PORT_METHODS, `${label}.method`),
    enabled: nullableBoolean(object.enabled, `${label}.enabled`),
    deviceDescriptions: stringArray(object.device_descriptions, `${label}.device_descriptions`),
    downstreamDeviceCount: nonnegativeInteger(
      object.downstream_device_count,
      `${label}.downstream_device_count`,
    ),
    storageLabels: stringArray(object.storage_labels, `${label}.storage_labels`),
    mountedLabels: stringArray(object.mounted_labels, `${label}.mounted_labels`),
    topologyLocations: stringArray(object.topology_locations, `${label}.topology_locations`),
  };
}

function decodeHub(value: unknown, index: number): UsbHub {
  const label = `usb_ports.hubs[${index}]`;
  const object = objectValue(value, label);
  return {
    location: stringValue(object.location, `${label}.location`),
    description: stringValue(object.description, `${label}.description`),
    detail: optionalNullableString(object.detail, `${label}.detail`),
    deviceId: nullableString(object.device_id, `${label}.device_id`),
    method: oneOf(object.method, PORT_METHODS, `${label}.method`),
    physical: booleanValue(object.physical, `${label}.physical`),
    advanced: booleanValue(object.advanced, `${label}.advanced`),
    ports: arrayValue(object.ports, `${label}.ports`).map((port, portIndex) =>
      decodePort(port, `${label}.ports[${portIndex}]`),
    ),
  };
}

const OPERATION_STATUSES = [
  'idle',
  'running',
  'complete',
  'error',
] as const satisfies readonly UsbPortOperationStatus[];
const PORT_ACTIONS = ['on', 'off', 'cycle', 'restore'] as const satisfies readonly UsbPortAction[];

function decodeOperation(value: unknown): UsbPortOperation {
  const object = objectValue(value, 'usb_ports.operation');
  const rawAction = object.action;
  return {
    status: oneOf(object.status, OPERATION_STATUSES, 'usb_ports.operation.status'),
    key: optionalNullableString(object.key, 'usb_ports.operation.key'),
    action:
      rawAction === undefined || rawAction === null
        ? null
        : oneOf(rawAction, PORT_ACTIONS, 'usb_ports.operation.action'),
    startedAt: optionalNullableNumber(object.started_at, 'usb_ports.operation.started_at'),
    completedAt: optionalNullableNumber(object.completed_at, 'usb_ports.operation.completed_at'),
    message: optionalNullableString(object.message, 'usb_ports.operation.message'),
    error: optionalNullableString(object.error, 'usb_ports.operation.error'),
  };
}

export function decodeUsbPortState(value: unknown): UsbPortState {
  const object = objectValue(value, 'usb_ports');
  return {
    loaded: booleanValue(object.loaded, 'usb_ports.loaded'),
    checkedAt: nullableNumber(object.checked_at, 'usb_ports.checked_at'),
    expiresAt: nullableNumber(object.expires_at, 'usb_ports.expires_at'),
    lastError: nullableString(object.last_error, 'usb_ports.last_error'),
    hubs: arrayValue(object.hubs, 'usb_ports.hubs').map(decodeHub),
    operation: decodeOperation(object.operation),
    expired: booleanValue(object.expired, 'usb_ports.expired'),
  };
}

export function decodeUsbStatusResponse(value: unknown): UsbStatus {
  const response = objectValue(value, 'USB status response');
  trueValue(response.ok, 'USB status response.ok');
  return {
    inventory: decodeInventory(response.usb),
    ports: decodeUsbPortState(response.usb_ports),
  };
}
