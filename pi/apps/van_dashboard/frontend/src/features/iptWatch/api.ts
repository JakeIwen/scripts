import { getJson, postJson } from '../../api/client';
import { decode } from '../../api/schema';
import {
  candidateSchema,
  jobSchema,
  statusSchema,
  wantlistSchema,
  type CandidateOperation,
  type WantRequest,
} from './schema';
const root = '/api/ipt-watch';
export const fetchStatus = async (signal: AbortSignal) =>
  decode(statusSchema, await getJson(`${root}/status`, signal), 'IPT watchlist');
export const check = async () =>
  decode(jobSchema, await postJson(`${root}/check`, {}), 'watchlist job');
export const wantlist = async (payload: WantRequest) =>
  decode(wantlistSchema, await postJson(`${root}/wantlist`, payload), 'wantlist');
export const candidate = async (action: CandidateOperation, torrent_id: string) =>
  decode(candidateSchema, await postJson(`${root}/${action}`, { torrent_id }), 'candidate');
