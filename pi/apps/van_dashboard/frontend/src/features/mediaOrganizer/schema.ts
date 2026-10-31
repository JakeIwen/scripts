import * as v from 'valibot';
import {
  booleanValue,
  finiteNumber,
  nonnegativeInteger,
  nullableString,
  object,
  oneOf,
  text,
} from '../../api/schema';

export const Category = { TV: 'TV', Documentaries: 'Documentaries', Movies: 'Movies' } as const;
export const categories = Object.values(Category);
export const Classifier = { Codex: 'codex', Offline: 'offline' } as const;
export const classifiers = Object.values(Classifier);
export const Stage = {
  Scan: 'scan',
  Propose: 'propose',
  Apply: 'apply',
  Undo: 'undo',
  Alias: 'alias',
  Mirror: 'mirror',
} as const;
export const PlanState = {
  Proposed: 'proposed',
  Applied: 'applied',
  Partial: 'partial',
  Discarded: 'discarded',
} as const;
export const ItemState = { Moved: 'moved', Failed: 'failed', Reverted: 'reverted' } as const;
export const RunMode = { Manual: 'manual', Auto: 'auto' } as const;
export const AliasState = { Scheduled: 'scheduled', Skipped: 'skipped', Failed: 'failed' } as const;
export const MirrorState = { Pending: 'pending', Settled: 'settled' } as const;
export const SkipReason = {
  Incomplete: 'incomplete',
  Moving: 'moving',
  Unreadable: 'unreadable',
  NotMedia: 'not-media',
  Hidden: 'hidden',
  Nested: 'nested',
} as const;
const category = oneOf(categories);
const classifier = oneOf(classifiers);
const confidence = v.pipe(finiteNumber, v.minValue(0), v.maxValue(1));
export const settingsSchema = object({
  auto_apply: booleanValue,
  auto_apply_min_confidence: confidence,
  auto_apply_new_shows: booleanValue,
  classifier,
  codex_model: text,
  mirror_rename: booleanValue,
  run_alias_after_apply: booleanValue,
});
const entrySchema = object({
  name: text,
  path: text,
  kind: oneOf(['dir', 'file']),
  size_bytes: nonnegativeInteger,
  torrent_hash: nullableString,
  torrent_name: nullableString,
  torrent_state: nullableString,
  tracked: booleanValue,
  complete: booleanValue,
  skip_reason: v.nullable(oneOf(Object.values(SkipReason))),
});
const proposalSchema = object({
  category,
  show: nullableString,
  confidence,
  reason: text,
  existing_show: booleanValue,
  source: classifier,
});
export const decisionSchema = object({ category, show: nullableString, accepted: booleanValue });
const rowSchema = object({
  entry: entrySchema,
  proposal: v.nullable(proposalSchema),
  decision: v.nullable(decisionSchema),
  target_path: nullableString,
  auto_eligible: booleanValue,
});
const planFields = {
  id: text,
  created_at: text,
  status: oneOf(Object.values(PlanState)),
  classifier,
  classifier_error: nullableString,
};
export const planSummarySchema = object({
  ...planFields,
  row_count: nonnegativeInteger,
  accepted_count: nonnegativeInteger,
  auto_count: nonnegativeInteger,
});
export const planSchema = object({
  ...planFields,
  existing_shows: v.record(category, v.array(text)),
  rows: v.array(rowSchema),
  run_ids: v.array(text),
  // Rows a run already moved or left unconfirmed; never edited or resubmitted.
  settled_rows: v.array(text),
  moved_rows: v.array(text),
});
const runFields = {
  id: text,
  plan_id: text,
  mode: oneOf(Object.values(RunMode)),
  started_at: text,
  finished_at: nullableString,
  alias_status: v.nullable(oneOf(Object.values(AliasState))),
  undoable: booleanValue,
  undone_at: nullableString,
  error: nullableString,
};
export const runSummarySchema = object({
  ...runFields,
  moved_count: nonnegativeInteger,
  failed_count: nonnegativeInteger,
  reverted_count: nonnegativeInteger,
});
export const runSchema = object({
  ...runFields,
  items: v.array(
    object({
      name: text,
      torrent_hash: nullableString,
      old_path: text,
      new_path: text,
      mirror_old: nullableString,
      mirror_new: nullableString,
      status: oneOf(Object.values(ItemState)),
      error: nullableString,
      mirror_state: v.nullable(oneOf(Object.values(MirrorState))),
    }),
  ),
});
export const jobSchema = object({
  stage: oneOf(Object.values(Stage)),
  started_at: text,
  finished_at: nullableString,
  ok: v.nullable(booleanValue),
  error: nullableString,
  result_id: nullableString,
});
export const statusSchema = object({
  media_mounted: booleanValue,
  mirror_available: booleanValue,
  qbittorrent_available: booleanValue,
  new_count: nonnegativeInteger,
  tracked_count: nonnegativeInteger,
  untracked_count: nonnegativeInteger,
  incomplete_count: nonnegativeInteger,
  latest_plan: v.nullable(planSummarySchema),
  recent_runs: v.array(runSummarySchema),
  settings: settingsSchema,
  busy: v.nullable(
    object({
      stage: oneOf(Object.values(Stage)),
      started_at: text,
      pid: v.optional(nonnegativeInteger),
      detail: v.optional(text),
      done: v.optional(nonnegativeInteger),
      total: v.optional(nonnegativeInteger),
    }),
  ),
  last_error: nullableString,
  mirror_pending: nonnegativeInteger,
  job: v.nullable(jobSchema),
});
export const aliasSchema = object({ status: v.literal(AliasState.Scheduled) });
export type OrganizerStatus = v.InferOutput<typeof statusSchema>;
export type Plan = v.InferOutput<typeof planSchema>;
export type PlanRow = Plan['rows'][number];
export type Decision = v.InferOutput<typeof decisionSchema>;
export type Settings = v.InferOutput<typeof settingsSchema>;
export type RunSummary = v.InferOutput<typeof runSummarySchema>;
export type Decisions = Record<string, Decision>;
