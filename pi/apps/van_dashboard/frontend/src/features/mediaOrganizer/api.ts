import { getJson, postJson } from '../../api/client';
import { decode } from '../../api/schema';
import {
  aliasSchema,
  jobSchema,
  planSchema,
  settingsSchema,
  Stage,
  statusSchema,
  type Decisions,
  type Settings,
} from './schema';
const root = '/api/media-organizer';
export const fetchStatus = async (signal: AbortSignal) =>
  decode(statusSchema, await getJson(`${root}/status`, signal), 'media organizer');
export const fetchPlan = async (id: string, signal?: AbortSignal) =>
  decode(planSchema, await getJson(`${root}/plans/${encodeURIComponent(id)}`, signal), 'plan');
export const decide = async (plan_id: string, decisions: Decisions) =>
  decode(planSchema, await postJson(`${root}/decide`, { plan_id, decisions }), 'plan');
export const saveSetting = async (changes: Partial<Settings>) =>
  decode(settingsSchema, await postJson(`${root}/settings`, changes), 'settings');
export const startJob = async (
  stage: typeof Stage.Propose | typeof Stage.Apply | typeof Stage.Undo,
  payload: object,
) => decode(jobSchema, await postJson(`${root}/${stage}`, payload), 'job');
export const rebuildLinks = async () =>
  decode(aliasSchema, await postJson(`${root}/alias`, {}), 'alias');
