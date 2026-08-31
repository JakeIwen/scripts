import { getJson, postForm } from '../../api/client';
import { objectValue, stringValue } from '../../api/validation';
import { decodeDealWatchSchedule, decodeDealWatchStatus } from './decoders';
import type {
  DealWatchListingDraft,
  DealWatchMutationResult,
  DealWatchSchedule,
  DealWatchSearchDraft,
  DealWatchStatus,
} from './types';

/** Load the authoritative Deal Watch state before rendering or mutating it. */
export async function fetchDealWatchStatus(signal: AbortSignal): Promise<DealWatchStatus> {
  const payload = await getJson('/api/price-checks', signal);
  return decodeDealWatchStatus(payload);
}

function mutationResult(payload: unknown, label: string): DealWatchMutationResult {
  const response = objectValue(payload, label);
  if (response.ok !== true) throw new TypeError(`${label}.ok must be true`);
  return { message: stringValue(response.message, `${label}.message`) };
}

function positiveId(id: number, label: string): string {
  if (!Number.isSafeInteger(id) || id <= 0) {
    throw new TypeError(`${label} must be a positive integer`);
  }
  return String(id);
}

function targetValue(target: number | 'all', label: string): string {
  return target === 'all' ? target : positiveId(target, label);
}

export async function addDealWatchListing(
  draft: DealWatchListingDraft,
): Promise<DealWatchMutationResult> {
  const payload = await postForm('/api/price-checks/add', {
    parser: draft.parser,
    threshold: draft.threshold,
    url: draft.url,
    title: draft.title,
  });
  return mutationResult(payload, 'add listing response');
}

export async function editDealWatchListing(
  id: number,
  draft: DealWatchListingDraft,
): Promise<DealWatchMutationResult> {
  const payload = await postForm('/api/price-checks/edit', {
    id: positiveId(id, 'listing ID'),
    parser: draft.parser,
    threshold: draft.threshold,
    url: draft.url,
    title: draft.title,
  });
  return mutationResult(payload, 'edit listing response');
}

export async function removeDealWatchListing(id: number): Promise<DealWatchMutationResult> {
  const payload = await postForm('/api/price-checks/remove', {
    id: positiveId(id, 'listing ID'),
  });
  return mutationResult(payload, 'remove listing response');
}

export async function setDealWatchListingMute(
  id: number,
  days: number,
): Promise<DealWatchMutationResult> {
  if (!Number.isSafeInteger(days) || days < 0) {
    throw new TypeError('mute days must be a non-negative integer');
  }
  const payload = await postForm('/api/price-checks/mute', {
    id: positiveId(id, 'listing ID'),
    days: String(days),
  });
  return mutationResult(payload, 'listing mute response');
}

export async function checkDealWatchListings(
  target: number | 'all',
): Promise<DealWatchMutationResult> {
  const payload = await postForm('/api/price-checks/check', {
    target: targetValue(target, 'listing target'),
  });
  return mutationResult(payload, 'listing check response');
}

export async function addDealWatchSearch(
  draft: DealWatchSearchDraft,
): Promise<DealWatchMutationResult> {
  const payload = await postForm('/api/price-checks/searches/add', {
    parser: draft.parser,
    url: draft.url,
    title: draft.title,
  });
  return mutationResult(payload, 'add search response');
}

export async function removeDealWatchSearch(id: number): Promise<DealWatchMutationResult> {
  const payload = await postForm('/api/price-checks/searches/remove', {
    id: positiveId(id, 'search ID'),
  });
  return mutationResult(payload, 'remove search response');
}

export async function dismissDealWatchSearchResult(
  searchId: number,
  itemId: string,
): Promise<DealWatchMutationResult> {
  if (!/^\d+$/.test(itemId)) throw new TypeError('search result ID must contain only digits');
  const payload = await postForm('/api/price-checks/searches/dismiss', {
    id: positiveId(searchId, 'search ID'),
    item_id: itemId,
  });
  return mutationResult(payload, 'dismiss search result response');
}

export async function checkDealWatchSearch(
  target: number | 'all',
): Promise<DealWatchMutationResult> {
  const payload = await postForm('/api/price-checks/searches/check', {
    target: targetValue(target, 'search target'),
  });
  return mutationResult(payload, 'search check response');
}

export async function previewDealWatchSchedule(
  expression: string,
  signal?: AbortSignal,
): Promise<DealWatchSchedule> {
  const payload = await postForm('/api/price-checks/schedule/parse', { expression }, signal);
  const response = objectValue(payload, 'schedule preview response');
  if (response.ok !== true) throw new TypeError('schedule preview response.ok must be true');
  return decodeDealWatchSchedule(response.schedule);
}

export async function saveDealWatchSchedule(expression: string): Promise<DealWatchMutationResult> {
  const payload = await postForm('/api/price-checks/schedule', { expression });
  return mutationResult(payload, 'save schedule response');
}
