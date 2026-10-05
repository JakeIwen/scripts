import * as v from 'valibot';

import {
  booleanValue,
  decode,
  nonnegativeInteger,
  nullableBoolean,
  nullableNumber,
  nullableString,
  object,
  oneOf,
  optionalNullableNumber,
  optionalNullableString,
  positiveInteger,
  stringArray,
  text,
  trueValue,
} from '../../api/schema';

export const usbDeviceStatusSchema = oneOf(['unplugged', 'partial', 'root', 'present']);
export const usbDeviceEventKindSchema = oneOf(['plugged', 'replugged', 'unplugged']);
export const usbPortMethodSchema = oneOf(['power', 'disable']);
export const usbPortActionSchema = oneOf(['on', 'off', 'cycle', 'restore']);
export const usbPortOperationStatusSchema = oneOf(['idle', 'running', 'complete', 'error']);

export const usbTopologyInstanceSchema = v.pipe(
  object({
    device_number: positiveInteger,
    location: text,
    parent_location: text,
    port: positiveInteger,
    labels: stringArray,
  }),
  v.transform((row) => ({
    deviceNumber: row.device_number,
    location: row.location,
    parentLocation: row.parent_location,
    port: row.port,
    labels: row.labels,
  })),
);
export const usbDeviceEventSchema = object({
  kind: usbDeviceEventKindSchema,
  at: nonnegativeInteger,
});
export const usbDeviceSchema = v.pipe(
  object({
    bus: text,
    device_id: text,
    description: text,
    present_count: nonnegativeInteger,
    labels: stringArray,
    root_hub: booleanValue,
    instances: v.array(usbTopologyInstanceSchema),
    max_count: positiveInteger,
    known_instances: v.array(usbTopologyInstanceSchema),
    event: v.nullable(usbDeviceEventSchema),
    status: usbDeviceStatusSchema,
  }),
  v.transform((row) => ({
    bus: row.bus,
    deviceId: row.device_id,
    description: row.description,
    presentCount: row.present_count,
    labels: row.labels,
    rootHub: row.root_hub,
    instances: row.instances,
    maxCount: row.max_count,
    knownInstances: row.known_instances,
    event: row.event,
    status: row.status,
  })),
);

const inventoryMetadataSchema = v.pipe(
  object({
    checked_at: nullableNumber,
    last_success_at: nullableNumber,
    last_error: nullableString,
    unplugged_device_count: nonnegativeInteger,
    storage_labels: stringArray,
  }),
  v.transform((row) => ({
    checkedAt: row.checked_at,
    lastSuccessAt: row.last_success_at,
    lastError: row.last_error,
    unpluggedDeviceCount: row.unplugged_device_count,
    storageLabels: row.storage_labels,
  })),
);

// Inventory validates devices and their count BEFORE metadata. Defer that second stage,
// rather than moving the cross-field error behind a potentially invalid timestamp.
export const usbInventorySchema = v.pipe(
  object({
    devices: v.array(usbDeviceSchema),
    present_device_count: nonnegativeInteger,
    checked_at: v.optional(v.unknown()),
    last_success_at: v.optional(v.unknown()),
    last_error: v.optional(v.unknown()),
    unplugged_device_count: v.optional(v.unknown()),
    storage_labels: v.optional(v.unknown()),
  }),
  v.forward(
    v.check(
      (row) =>
        row.present_device_count ===
        row.devices.reduce(
          (count, device) => count + (device.rootHub ? 0 : device.presentCount),
          0,
        ),
      'does not match usb.devices',
    ),
    ['present_device_count'],
  ),
  v.transform((row) => ({
    ...decode(inventoryMetadataSchema, row, 'usb'),
    presentDeviceCount: row.present_device_count,
    devices: row.devices,
  })),
);

export const usbPortSchema = v.pipe(
  object({
    key: text,
    location: text,
    port: positiveInteger,
    method: usbPortMethodSchema,
    enabled: nullableBoolean,
    device_descriptions: stringArray,
    downstream_device_count: nonnegativeInteger,
    storage_labels: stringArray,
    mounted_labels: stringArray,
    topology_locations: stringArray,
  }),
  v.transform((row) => ({
    key: row.key,
    location: row.location,
    port: row.port,
    method: row.method,
    enabled: row.enabled,
    deviceDescriptions: row.device_descriptions,
    downstreamDeviceCount: row.downstream_device_count,
    storageLabels: row.storage_labels,
    mountedLabels: row.mounted_labels,
    topologyLocations: row.topology_locations,
  })),
);
export const usbHubSchema = v.pipe(
  object({
    location: text,
    description: text,
    detail: optionalNullableString,
    device_id: nullableString,
    method: usbPortMethodSchema,
    physical: booleanValue,
    advanced: booleanValue,
    ports: v.array(usbPortSchema),
  }),
  v.transform((row) => ({
    location: row.location,
    description: row.description,
    detail: row.detail,
    deviceId: row.device_id,
    method: row.method,
    physical: row.physical,
    advanced: row.advanced,
    ports: row.ports,
  })),
);
export const usbPortOperationSchema = v.pipe(
  object({
    status: usbPortOperationStatusSchema,
    key: optionalNullableString,
    action: v.optional(v.nullable(usbPortActionSchema), null),
    started_at: optionalNullableNumber,
    completed_at: optionalNullableNumber,
    message: optionalNullableString,
    error: optionalNullableString,
  }),
  v.transform((row) => ({
    status: row.status,
    key: row.key,
    action: row.action,
    startedAt: row.started_at,
    completedAt: row.completed_at,
    message: row.message,
    error: row.error,
  })),
);
export const usbPortStateSchema = v.pipe(
  object({
    loaded: booleanValue,
    checked_at: nullableNumber,
    expires_at: nullableNumber,
    last_error: nullableString,
    hubs: v.array(usbHubSchema),
    operation: usbPortOperationSchema,
    expired: booleanValue,
  }),
  v.transform((row) => ({
    loaded: row.loaded,
    checkedAt: row.checked_at,
    expiresAt: row.expires_at,
    lastError: row.last_error,
    hubs: row.hubs,
    operation: row.operation,
    expired: row.expired,
  })),
);

// The existing API uses independent root labels for the envelope and nested resources.
export const usbStatusResponseSchema = v.pipe(
  object({ ok: trueValue, usb: v.optional(v.unknown()), usb_ports: v.optional(v.unknown()) }),
  v.transform((row) => ({
    inventory: decode(usbInventorySchema, row.usb, 'usb'),
    ports: decodeUsbPortState(row.usb_ports),
  })),
);
export const usbPortMutationSchema = v.pipe(
  object({ ok: trueValue, message: text, usb_ports: v.optional(v.unknown()) }),
  v.transform((row) => ({ message: row.message, ports: decodeUsbPortState(row.usb_ports) })),
);

export function decodeUsbPortState(value: unknown): UsbPortState {
  return decode(usbPortStateSchema, value, 'usb_ports');
}
export function decodeUsbStatusResponse(value: unknown): UsbStatus {
  return decode(usbStatusResponseSchema, value, 'USB status response');
}
export function decodeMutation(value: unknown, label: string): UsbPortMutationResult {
  return decode(usbPortMutationSchema, value, label);
}

export type UsbDeviceStatus = v.InferOutput<typeof usbDeviceStatusSchema>;
export type UsbDeviceEventKind = v.InferOutput<typeof usbDeviceEventKindSchema>;
export type UsbTopologyInstance = v.InferOutput<typeof usbTopologyInstanceSchema>;
export type UsbDeviceEvent = v.InferOutput<typeof usbDeviceEventSchema>;
export type UsbDevice = v.InferOutput<typeof usbDeviceSchema>;
export type UsbInventory = v.InferOutput<typeof usbInventorySchema>;
export type UsbPortMethod = v.InferOutput<typeof usbPortMethodSchema>;
export type UsbPortAction = v.InferOutput<typeof usbPortActionSchema>;
export type UsbPortOperationStatus = v.InferOutput<typeof usbPortOperationStatusSchema>;
export type UsbPort = v.InferOutput<typeof usbPortSchema>;
export type UsbHub = v.InferOutput<typeof usbHubSchema>;
export type UsbPortOperation = v.InferOutput<typeof usbPortOperationSchema>;
export type UsbPortState = v.InferOutput<typeof usbPortStateSchema>;
export type UsbStatus = v.InferOutput<typeof usbStatusResponseSchema>;
export type UsbPortMutationResult = v.InferOutput<typeof usbPortMutationSchema>;
