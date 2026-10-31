import * as v from 'valibot';

import {
  booleanValue,
  decode,
  finiteNumber,
  nonnegativeInteger,
  nullableBoolean,
  nullableNumber,
  nullableString,
  object,
  oneOf,
  stringArray,
  text,
  trueValue,
} from '../../../api/schema';

const modeValue = oneOf(['auto', 'metered', 'unmetered']);
const approvalSchema = object({
  kind: oneOf(['until', 'episode']),
  until: nullableNumber,
  episode: nullableString,
  set_by: text,
  set_at: finiteNumber,
});
const configSchema = object({
  schema: v.literal(1),
  revision: nonnegativeInteger,
  metered_ssids: stringArray,
  home_network: object({ ssid: text, gateway: text, gateway_mac: text }),
  receipts: v.optional(v.array(object({ op_id: text, digest: text })), []),
});
const policySchema = object({
  schema: v.literal(1),
  revision: nonnegativeInteger,
  mode: object({ value: modeValue, until: nullableNumber, set_by: text, set_at: finiteNumber }),
  approvals: v.record(text, approvalSchema),
  exempt: stringArray,
});
const consumerStateSchema = object({
  permitted: booleanValue,
  reason: text,
  approval: v.nullable(approvalSchema),
  actual: oneOf(['running', 'held', 'stopped_manually', 'unknown', 'n/a']),
});
const hostStateSchema = object({
  host: oneOf(['pi', 'mac']),
  evaluated_at: finiteNumber,
  verdict: object({
    metered: nullableBoolean,
    reason: text,
    matched: v.array(object({ member: text, ssid: text })),
    evidence_at: nullableNumber,
  }),
  effective: object({
    metered: booleanValue,
    source: oneOf(['verdict', 'mode']),
    episode: nullableString,
  }),
  consumers: v.record(text, consumerStateSchema),
  // Invalid evaluations deliberately carry -1 revisions, not an absent state.
  config_revision: finiteNumber,
  policy_revision: finiteNumber,
  route_signature: nullableString,
  valid_until: nullableNumber,
});
const consumerSchema = object({
  id: text,
  host: oneOf(['pi', 'mac']),
  label: text,
  how: oneOf(['tick', 'guard', 'run', 'external', 'advisory', 'unprotected']),
  note: text,
});
const statusSchema = object({
  ok: trueValue,
  config: configSchema,
  policy: policySchema,
  state: hostStateSchema,
  registry: v.array(consumerSchema),
  mac_report: v.nullable(hostStateSchema),
});
const ssidResponseSchema = object({ ok: trueValue, config: configSchema });
const policyResponseSchema = object({ ok: trueValue, policy: policySchema });

export const decodeMeteredStatus = (value: unknown): MeteredStatus =>
  decode(statusSchema, value, 'metered');
export const decodeSSIDResponse = (value: unknown) =>
  decode(ssidResponseSchema, value, 'metered SSIDs');
export const decodePolicyResponse = (value: unknown) =>
  decode(policyResponseSchema, value, 'metered policy');

export type MeteredStatus = v.InferOutput<typeof statusSchema>;
export type HostState = v.InferOutput<typeof hostStateSchema>;
export type ConsumerState = v.InferOutput<typeof consumerStateSchema>;
export type Consumer = v.InferOutput<typeof consumerSchema>;
export type ModeValue = v.InferOutput<typeof modeValue>;

export type PolicyEdit =
  | { op: 'mode'; value: ModeValue; seconds?: number }
  | { op: 'approve'; consumer: string; value: 'until'; seconds: number }
  | { op: 'approve'; consumer: string; value: 'episode' }
  | { op: 'revoke'; consumer: string }
  | { op: 'exempt'; consumer: string; enabled: boolean };
