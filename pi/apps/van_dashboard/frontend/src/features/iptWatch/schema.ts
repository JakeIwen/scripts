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

export const Source = { Library: 'library', Manual: 'manual' } as const;
export const CandidateState = {
  New: 'new',
  Downloaded: 'downloaded',
  Dismissed: 'dismissed',
} as const;
export const Login = { Ok: 'ok', Needed: 'needed', Unknown: 'unknown' } as const;
export const WantAction = {
  Add: 'add',
  Remove: 'remove',
  Pause: 'pause',
  Resume: 'resume',
} as const;
export const CandidateAction = { Download: 'download', Dismiss: 'dismiss' } as const;
export const JobStage = { Check: 'check' } as const;
export const wantedSchema = object({
  name: text,
  source: oneOf(Object.values(Source)),
  library_show_id: nullableString,
  last_watched: v.nullable(finiteNumber),
  have_season: v.nullable(nonnegativeInteger),
  have_episode: v.nullable(nonnegativeInteger),
  added_at: text,
  paused: booleanValue,
});
export const candidateSchema = object({
  torrent_id: text,
  name: text,
  show: text,
  season: v.nullable(nonnegativeInteger),
  episode: v.nullable(nonnegativeInteger),
  size_bytes: nonnegativeInteger,
  seeders: nonnegativeInteger,
  leechers: nonnegativeInteger,
  snatches: nonnegativeInteger,
  freeleech: booleanValue,
  age_text: text,
  category: text,
  download_path: text,
  first_seen: text,
  status: oneOf(Object.values(CandidateState)),
  downloaded_at: nullableString,
  file_path: nullableString,
  error: nullableString,
});
export const jobSchema = object({
  stage: oneOf(Object.values(JobStage)),
  started_at: text,
  finished_at: nullableString,
  ok: v.nullable(booleanValue),
  error: nullableString,
  result_id: nullableString,
});
export const wantlistSchema = object({ wanted: v.array(wantedSchema) });
export const statusSchema = object({
  login: oneOf(Object.values(Login)),
  last_check: nullableString,
  last_error: nullableString,
  checking: booleanValue,
  wanted_count: nonnegativeInteger,
  new_candidate_count: nonnegativeInteger,
  tfiles_writable: booleanValue,
  library_available: booleanValue,
  wanted: v.array(wantedSchema),
  candidates: v.array(candidateSchema),
  job: v.nullable(jobSchema),
});
export type WatchStatus = v.InferOutput<typeof statusSchema>;
export type WantedShow = v.InferOutput<typeof wantedSchema>;
export type Candidate = v.InferOutput<typeof candidateSchema>;
export type WantlistAction = (typeof WantAction)[keyof typeof WantAction];
export type CandidateOperation = (typeof CandidateAction)[keyof typeof CandidateAction];
export interface WantRequest {
  action: WantlistAction;
  name: string;
  season?: number;
  episode?: number;
}
