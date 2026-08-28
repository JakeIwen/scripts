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
import { decodeExternalHttpUrl } from './externalLinks';
import type {
  DealWatchCheckState,
  DealWatchListing,
  DealWatchListingSummary,
  DealWatchSavedSearch,
  DealWatchSchedule,
  DealWatchScheduleErrorCode,
  DealWatchSearchResult,
  DealWatchSearchSummary,
  DealWatchStatus,
} from './types';

const CHECK_STATES = ['never', 'ok', 'error'] as const satisfies readonly DealWatchCheckState[];
const SCHEDULE_ERROR_CODES = [
  'parse',
  'rate_limit',
] as const satisfies readonly DealWatchScheduleErrorCode[];

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

function nonemptyString(value: unknown, label: string): string {
  const text = stringValue(value, label);
  if (!text.trim()) throw new TypeError(`${label} must not be empty`);
  return text;
}

function nullableNonemptyString(value: unknown, label: string): string | null {
  const text = nullableString(value, label);
  if (text !== null && !text.trim()) {
    throw new TypeError(`${label} must not be empty when present`);
  }
  return text;
}

function nonnegativeInteger(value: unknown, label: string): number {
  const number = numberValue(value, label);
  if (!Number.isSafeInteger(number) || number < 0) {
    throw new TypeError(`${label} must be a non-negative safe integer`);
  }
  return number;
}

function positiveInteger(value: unknown, label: string): number {
  const number = nonnegativeInteger(value, label);
  if (number === 0) throw new TypeError(`${label} must be greater than zero`);
  return number;
}

function nullableNonnegativeInteger(value: unknown, label: string): number | null {
  const number = nullableNumber(value, label);
  return number === null ? null : nonnegativeInteger(number, label);
}

function decodeMoneyPair(
  textValue: unknown,
  centsValue: unknown,
  label: string,
): { text: string; cents: number } {
  const text = stringValue(textValue, label);
  const match = /^(\d+)\.(\d{2})$/.exec(text);
  const cents = positiveInteger(centsValue, `${label}_cents`);
  if (!match) throw new TypeError(`${label} must use dollars and exactly two decimals`);

  const dollars = Number(match[1]);
  const fractionalCents = Number(match[2]);
  const representedCents = dollars * 100 + fractionalCents;
  if (!Number.isSafeInteger(representedCents) || representedCents !== cents) {
    throw new TypeError(`${label} disagrees with ${label}_cents`);
  }
  return { text, cents };
}

function decodeNullableMoneyPair(
  textValue: unknown,
  centsValue: unknown,
  label: string,
): { text: string | null; cents: number | null } {
  if (textValue === null && centsValue === null) return { text: null, cents: null };
  if (textValue === null || centsValue === null) {
    throw new TypeError(`${label} and ${label}_cents must both be present or null`);
  }
  return decodeMoneyPair(textValue, centsValue, label);
}

function decodeListing(value: unknown, index: number): DealWatchListing {
  const label = `price checks.items[${index}]`;
  const item = objectValue(value, label);
  const id = positiveInteger(item.id, `${label}.id`);
  const parser = oneOf(item.parser, ['amazon'] as const, `${label}.parser`);
  const threshold = decodeMoneyPair(item.threshold, item.threshold_cents, `${label}.threshold`);
  const url = decodeExternalHttpUrl(item.url, `${label}.url`);
  const title = nullableNonemptyString(item.title, `${label}.title`);
  const displayTitle = nonemptyString(item.display_title, `${label}.display_title`);
  const createdAt = nonnegativeInteger(item.created_at, `${label}.created_at`);
  const updatedAt = nonnegativeInteger(item.updated_at, `${label}.updated_at`);
  const lastCheckedAt = nullableNonnegativeInteger(
    item.last_checked_at,
    `${label}.last_checked_at`,
  );
  const lastPrice = decodeNullableMoneyPair(
    item.last_price,
    item.last_price_cents,
    `${label}.last_price`,
  );
  const lastPriceCheckedAt = nullableNonnegativeInteger(
    item.last_price_checked_at,
    `${label}.last_price_checked_at`,
  );
  const lastStatus = oneOf(item.last_status, CHECK_STATES, `${label}.last_status`);
  const lastError = nullableNonemptyString(item.last_error, `${label}.last_error`);
  const lastTitle = nullableNonemptyString(item.last_title, `${label}.last_title`);
  const notifyMutedUntil = nullableNonnegativeInteger(
    item.notify_muted_until,
    `${label}.notify_muted_until`,
  );
  const notificationsMuted = booleanValue(item.notifications_muted, `${label}.notifications_muted`);
  const belowThreshold = nullableBoolean(item.below_threshold, `${label}.below_threshold`);

  if (updatedAt < createdAt) {
    throw new TypeError(`${label}.updated_at must not precede created_at`);
  }
  if (lastCheckedAt !== null && lastCheckedAt > updatedAt) {
    throw new TypeError(`${label}.last_checked_at must not follow updated_at`);
  }
  if (displayTitle !== (title ?? lastTitle ?? url)) {
    throw new TypeError(`${label}.display_title does not match its title fallback`);
  }
  if ((lastPrice.text === null) !== (lastPriceCheckedAt === null)) {
    throw new TypeError(`${label}.last_price_checked_at does not match its saved price`);
  }
  if (lastPriceCheckedAt !== null && lastCheckedAt !== null && lastPriceCheckedAt > lastCheckedAt) {
    throw new TypeError(`${label}.last_price_checked_at must not follow last_checked_at`);
  }
  const expectedBelowThreshold =
    lastPrice.cents === null ? null : lastPrice.cents < threshold.cents;
  if (belowThreshold !== expectedBelowThreshold) {
    throw new TypeError(`${label}.below_threshold disagrees with the returned prices`);
  }
  if (notificationsMuted && notifyMutedUntil === null) {
    throw new TypeError(`${label}.notifications_muted requires notify_muted_until`);
  }

  if (lastStatus === 'never') {
    if (lastCheckedAt !== null || lastError !== null || lastPrice.text !== null) {
      throw new TypeError(`${label} has check evidence despite a never status`);
    }
  } else if (lastStatus === 'error') {
    if (lastCheckedAt === null || lastError === null) {
      throw new TypeError(`${label} error status requires a check time and error`);
    }
  } else if (
    lastCheckedAt === null ||
    lastError !== null ||
    lastPrice.text === null ||
    lastPriceCheckedAt !== lastCheckedAt
  ) {
    throw new TypeError(`${label} successful status has inconsistent check evidence`);
  }

  return {
    id,
    parser,
    threshold: threshold.text,
    thresholdCents: threshold.cents,
    url,
    title,
    displayTitle,
    createdAt,
    updatedAt,
    lastCheckedAt,
    lastPrice: lastPrice.text,
    lastPriceCents: lastPrice.cents,
    lastPriceCheckedAt,
    lastStatus,
    lastError,
    lastTitle,
    notifyMutedUntil,
    notificationsMuted,
    belowThreshold,
  };
}

function decodeSearchResult(
  value: unknown,
  index: number,
  parentLabel: string,
): DealWatchSearchResult {
  const label = `${parentLabel}.results[${index}]`;
  const result = objectValue(value, label);
  const dismissed = booleanValue(result.dismissed, `${label}.dismissed`);
  const dismissedAt = nullableNumber(result.dismissed_at, `${label}.dismissed_at`);
  if (dismissed || dismissedAt !== null) {
    throw new TypeError(`${label} must be a visible, non-dismissed result`);
  }

  const imageUrl = nullableString(result.image_url, `${label}.image_url`);
  if (imageUrl !== null) {
    decodeExternalHttpUrl(imageUrl, `${label}.image_url`, {
      allowProtocolRelative: true,
    });
  }
  const firstSeenAt = nonnegativeInteger(result.first_seen_at, `${label}.first_seen_at`);
  const lastSeenAt = nonnegativeInteger(result.last_seen_at, `${label}.last_seen_at`);
  if (lastSeenAt < firstSeenAt) {
    throw new TypeError(`${label}.last_seen_at must not precede first_seen_at`);
  }

  return {
    itemId: nonemptyString(result.item_id, `${label}.item_id`),
    title: nonemptyString(result.title, `${label}.title`),
    url: decodeExternalHttpUrl(result.url, `${label}.url`),
    price: nullableNonemptyString(result.price, `${label}.price`),
    shipping: nullableNonemptyString(result.shipping, `${label}.shipping`),
    imageUrl,
    firstSeenAt,
    lastSeenAt,
    dismissed: false,
    dismissedAt: null,
  };
}

function decodeSavedSearch(value: unknown, index: number): DealWatchSavedSearch {
  const label = `price checks.searches[${index}]`;
  const search = objectValue(value, label);
  const id = positiveInteger(search.id, `${label}.id`);
  const parser = oneOf(search.parser, ['ebay'] as const, `${label}.parser`);
  const url = decodeExternalHttpUrl(search.url, `${label}.url`);
  const title = nullableNonemptyString(search.title, `${label}.title`);
  const displayTitle = nonemptyString(search.display_title, `${label}.display_title`);
  const createdAt = nonnegativeInteger(search.created_at, `${label}.created_at`);
  const updatedAt = nonnegativeInteger(search.updated_at, `${label}.updated_at`);
  const lastCheckedAt = nullableNonnegativeInteger(
    search.last_checked_at,
    `${label}.last_checked_at`,
  );
  const lastStatus = oneOf(search.last_status, CHECK_STATES, `${label}.last_status`);
  const lastError = nullableNonemptyString(search.last_error, `${label}.last_error`);
  const resultCount = nonnegativeInteger(search.result_count, `${label}.result_count`);
  const hiddenCurrentCount = nonnegativeInteger(
    search.hidden_current_count,
    `${label}.hidden_current_count`,
  );
  const dismissedCount = nonnegativeInteger(search.dismissed_count, `${label}.dismissed_count`);
  const knownCount = nonnegativeInteger(search.known_count, `${label}.known_count`);
  const results = arrayValue(search.results, `${label}.results`).map((result, resultIndex) =>
    decodeSearchResult(result, resultIndex, label),
  );

  if (updatedAt < createdAt) {
    throw new TypeError(`${label}.updated_at must not precede created_at`);
  }
  if (lastCheckedAt !== null && lastCheckedAt > updatedAt) {
    throw new TypeError(`${label}.last_checked_at must not follow updated_at`);
  }
  if (displayTitle !== (title ?? url)) {
    throw new TypeError(`${label}.display_title does not match its title fallback`);
  }
  if (resultCount !== results.length) {
    throw new TypeError(`${label}.result_count does not match returned results`);
  }
  if (
    hiddenCurrentCount > dismissedCount ||
    dismissedCount > knownCount ||
    resultCount + dismissedCount > knownCount
  ) {
    throw new TypeError(`${label} contains inconsistent result counts`);
  }
  const resultIds = new Set(results.map((result) => result.itemId));
  if (resultIds.size !== results.length) {
    throw new TypeError(`${label}.results contains duplicate item IDs`);
  }

  if (lastStatus === 'never') {
    if (lastCheckedAt !== null || lastError !== null) {
      throw new TypeError(`${label} has check evidence despite a never status`);
    }
  } else if (lastStatus === 'error') {
    if (lastCheckedAt === null || lastError === null) {
      throw new TypeError(`${label} error status requires a check time and error`);
    }
  } else if (lastCheckedAt === null || lastError !== null) {
    throw new TypeError(`${label} successful status has inconsistent check evidence`);
  }

  return {
    id,
    parser,
    url,
    title,
    displayTitle,
    createdAt,
    updatedAt,
    lastCheckedAt,
    lastStatus,
    lastError,
    resultCount,
    hiddenCurrentCount,
    dismissedCount,
    knownCount,
    results,
  };
}

function decodeListingSummary(value: unknown): DealWatchListingSummary {
  const summary = objectValue(value, 'price checks.summary');
  return {
    count: nonnegativeInteger(summary.count, 'price checks.summary.count'),
    checked: nonnegativeInteger(summary.checked, 'price checks.summary.checked'),
    belowThreshold: nonnegativeInteger(
      summary.below_threshold,
      'price checks.summary.below_threshold',
    ),
    errors: nonnegativeInteger(summary.errors, 'price checks.summary.errors'),
  };
}

function decodeSearchSummary(value: unknown): DealWatchSearchSummary {
  const summary = objectValue(value, 'price checks.search_summary');
  return {
    count: nonnegativeInteger(summary.count, 'price checks.search_summary.count'),
    checked: nonnegativeInteger(summary.checked, 'price checks.search_summary.checked'),
    results: nonnegativeInteger(summary.results, 'price checks.search_summary.results'),
    errors: nonnegativeInteger(summary.errors, 'price checks.search_summary.errors'),
  };
}

function listingSummariesMatch(
  returned: DealWatchListingSummary,
  observed: DealWatchListingSummary,
): boolean {
  return (
    returned.count === observed.count &&
    returned.checked === observed.checked &&
    returned.belowThreshold === observed.belowThreshold &&
    returned.errors === observed.errors
  );
}

function searchSummariesMatch(
  returned: DealWatchSearchSummary,
  observed: DealWatchSearchSummary,
): boolean {
  return (
    returned.count === observed.count &&
    returned.checked === observed.checked &&
    returned.results === observed.results &&
    returned.errors === observed.errors
  );
}

export function decodeDealWatchSchedule(value: unknown): DealWatchSchedule {
  const schedule = objectValue(value, 'price checks.schedule');
  const expression = stringValue(schedule.expression, 'price checks.schedule.expression');
  const description = stringValue(schedule.description, 'price checks.schedule.description');
  const error = nullableNonemptyString(schedule.error, 'price checks.schedule.error');
  const rawErrorCode = schedule.error_code;
  const errorCode =
    rawErrorCode === null
      ? null
      : oneOf(rawErrorCode, SCHEDULE_ERROR_CODES, 'price checks.schedule.error_code');

  if (error === null) {
    if (!expression.trim() || !description.trim() || errorCode !== null) {
      throw new TypeError('successful price-check schedule is incomplete');
    }
  } else if (description !== '' || errorCode === null) {
    throw new TypeError('failed price-check schedule has inconsistent error details');
  }

  return { expression, description, error, errorCode };
}

/** Decode and cross-check the complete GET /api/price-checks snapshot. */
export function decodeDealWatchStatus(payload: unknown): DealWatchStatus {
  const response = objectValue(payload, 'price checks response');
  trueValue(response.ok, 'price checks response.ok');

  const listings = arrayValue(response.items, 'price checks.items').map(decodeListing);
  const searches = arrayValue(response.searches, 'price checks.searches').map(decodeSavedSearch);
  const listingSummary = decodeListingSummary(response.summary);
  const searchSummary = decodeSearchSummary(response.search_summary);

  if (new Set(listings.map((item) => item.id)).size !== listings.length) {
    throw new TypeError('price checks.items contains duplicate IDs');
  }
  if (new Set(searches.map((search) => search.id)).size !== searches.length) {
    throw new TypeError('price checks.searches contains duplicate IDs');
  }

  const observedListingSummary: DealWatchListingSummary = {
    count: listings.length,
    checked: listings.filter((item) => item.lastCheckedAt !== null).length,
    belowThreshold: listings.filter((item) => item.belowThreshold === true).length,
    errors: listings.filter((item) => item.lastStatus === 'error').length,
  };
  const observedSearchSummary: DealWatchSearchSummary = {
    count: searches.length,
    checked: searches.filter((search) => search.lastCheckedAt !== null).length,
    results: searches.reduce((total, search) => total + search.resultCount, 0),
    errors: searches.filter((search) => search.lastStatus === 'error').length,
  };

  if (!listingSummariesMatch(listingSummary, observedListingSummary)) {
    throw new TypeError('price checks.summary does not match price checks.items');
  }
  if (!searchSummariesMatch(searchSummary, observedSearchSummary)) {
    throw new TypeError('price checks.search_summary does not match price checks.searches');
  }

  return {
    listings,
    listingSummary,
    searches,
    searchSummary,
    schedule: decodeDealWatchSchedule(response.schedule),
  };
}
