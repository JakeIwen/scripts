import {
  arrayValue,
  booleanValue,
  nullableNumber,
  nullableString,
  numberValue,
  objectValue,
  stringValue,
} from '../../api/validation';
import { decodeExternalHttpUrl } from './externalLinks';
import type {
  DealWatchCheckState,
  DealWatchHavenoOffer,
  DealWatchHavenoReferenceSource,
  DealWatchHavenoSummary,
  DealWatchHavenoWatch,
} from './types';

const CHECK_STATES = ['never', 'ok', 'error'] as const satisfies readonly DealWatchCheckState[];
const REFERENCE_SOURCES = [
  'haveno-pricenode',
  'kraken',
] as const satisfies readonly DealWatchHavenoReferenceSource[];
const PRICE_PATTERN = /^\d+\.\d{2}$/;
const PCT_PATTERN = /^-?\d+\.\d{2}$/;
const XMR_PATTERN = /^(?:0\.\d{0,11}[1-9]|[1-9]\d*(?:\.\d{0,11}[1-9])?)$/;
const USD_PATTERN = /^(?:0\.\d?[1-9]|[1-9]\d*(?:\.\d?[1-9])?)$/;

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

function nullableTimestamp(value: unknown, label: string): number | null {
  const number = nullableNumber(value, label);
  return number === null ? null : nonnegativeInteger(number, label);
}

function decimalString(value: unknown, pattern: RegExp, label: string): string {
  const text = stringValue(value, label);
  if (!pattern.test(text) || !Number.isFinite(Number(text))) {
    throw new TypeError(`${label} has an invalid decimal format`);
  }
  return text;
}

function priceString(value: unknown, label: string): string {
  const text = decimalString(value, PRICE_PATTERN, label);
  if (Number(text) <= 0) throw new TypeError(`${label} must be greater than zero`);
  return text;
}

function nullablePriceString(value: unknown, label: string): string | null {
  return value === null ? null : priceString(value, label);
}

function pctString(value: unknown, label: string): string {
  return decimalString(value, PCT_PATTERN, label);
}

function nullablePctString(value: unknown, label: string): string | null {
  return value === null ? null : pctString(value, label);
}

function xmrString(value: unknown, label: string): string {
  return decimalString(value, XMR_PATTERN, label);
}

function nullableXmrString(value: unknown, label: string): string | null {
  return value === null ? null : xmrString(value, label);
}

function nullableUsdString(value: unknown, label: string): string | null {
  return value === null ? null : decimalString(value, USD_PATTERN, label);
}

function decodeOffer(value: unknown, index: number, parentLabel: string): DealWatchHavenoOffer {
  const label = `${parentLabel}.offers[${index}]`;
  const offer = objectValue(value, label);
  const minXmr = nullableXmrString(offer.min_xmr, `${label}.min_xmr`);
  const maxXmr = nullableXmrString(offer.max_xmr, `${label}.max_xmr`);
  const firstSeenAt = nonnegativeInteger(offer.first_seen_at, `${label}.first_seen_at`);
  const lastSeenAt = nonnegativeInteger(offer.last_seen_at, `${label}.last_seen_at`);
  if (minXmr !== null && maxXmr !== null && Number(minXmr) > Number(maxXmr)) {
    throw new TypeError(`${label}.min_xmr must not exceed max_xmr`);
  }
  if (lastSeenAt < firstSeenAt) {
    throw new TypeError(`${label}.last_seen_at must not precede first_seen_at`);
  }
  return {
    offerId: nonemptyString(offer.offer_id, `${label}.offer_id`),
    price: priceString(offer.price, `${label}.price`),
    vsMarketPct: pctString(offer.vs_market_pct, `${label}.vs_market_pct`),
    effectivePct: pctString(offer.effective_pct, `${label}.effective_pct`),
    minXmr,
    maxXmr,
    offeredAt: nonnegativeInteger(offer.offered_at, `${label}.offered_at`),
    firstSeenAt,
    lastSeenAt,
    matches: booleanValue(offer.matches, `${label}.matches`),
    notifiedAt: nullableTimestamp(offer.notified_at, `${label}.notified_at`),
  };
}

function decodeReferenceSource(
  value: unknown,
  label: string,
): DealWatchHavenoReferenceSource | null {
  return value === null ? null : oneOf(value, REFERENCE_SOURCES, label);
}

function validateWatchState(watch: DealWatchHavenoWatch, label: string): void {
  if (watch.updatedAt < watch.createdAt) {
    throw new TypeError(`${label}.updated_at must not precede created_at`);
  }
  if (watch.lastCheckedAt !== null && watch.lastCheckedAt > watch.updatedAt) {
    throw new TypeError(`${label}.last_checked_at must not follow updated_at`);
  }
  if ((watch.referencePrice === null) !== (watch.referenceSource === null)) {
    throw new TypeError(`${label} has incomplete reference price details`);
  }
  if ((watch.referencePrice === null) !== (watch.referenceAt === null)) {
    throw new TypeError(`${label} has incomplete reference price details`);
  }
  if ((watch.bestPrice === null) !== (watch.bestVsMarketPct === null)) {
    throw new TypeError(`${label} has incomplete best offer details`);
  }
  if (watch.notificationsMuted && watch.notifyMutedUntil === null) {
    throw new TypeError(`${label}.notifications_muted requires notify_muted_until`);
  }
  if (watch.lastStatus === 'never') {
    if (watch.lastCheckedAt !== null || watch.lastError !== null) {
      throw new TypeError(`${label} has check evidence despite a never status`);
    }
  } else if (watch.lastStatus === 'error') {
    if (watch.lastCheckedAt === null || watch.lastError === null) {
      throw new TypeError(`${label} error status requires a check time and error`);
    }
  } else if (watch.lastCheckedAt === null || watch.lastError !== null) {
    throw new TypeError(`${label} successful status has inconsistent check evidence`);
  }
}

function validateOffers(watch: DealWatchHavenoWatch, label: string): void {
  if (watch.offers.length > 20 || watch.offerCount < watch.offers.length) {
    throw new TypeError(`${label}.offers disagrees with offer_count`);
  }
  if (watch.matchCount !== watch.offers.filter((offer) => offer.matches).length) {
    throw new TypeError(`${label}.match_count does not match returned offers`);
  }
  if (new Set(watch.offers.map((offer) => offer.offerId)).size !== watch.offers.length) {
    throw new TypeError(`${label}.offers contains duplicate offer IDs`);
  }
  for (let index = 1; index < watch.offers.length; index += 1) {
    if (Number(watch.offers[index - 1]!.price) > Number(watch.offers[index]!.price)) {
      throw new TypeError(`${label}.offers must be sorted by price`);
    }
  }
  const best = watch.offers[0] ?? null;
  if (best === null) {
    if (watch.bestPrice !== null || watch.bestVsMarketPct !== null || watch.offerCount !== 0) {
      throw new TypeError(`${label} has inconsistent empty offer details`);
    }
  } else if (watch.bestPrice !== best.price || watch.bestVsMarketPct !== best.vsMarketPct) {
    throw new TypeError(`${label} best offer does not match its first returned offer`);
  }
}

function decodeWatch(value: unknown, index: number): DealWatchHavenoWatch {
  const label = `price checks.haveno_watches[${index}]`;
  const item = objectValue(value, label);
  const thresholdPct = pctString(item.threshold_pct, `${label}.threshold_pct`);
  if (Number(thresholdPct) < -50 || Number(thresholdPct) > 50) {
    throw new TypeError(`${label}.threshold_pct must be between -50 and 50`);
  }
  const feePct = pctString(item.fee_pct, `${label}.fee_pct`);
  if (Number(feePct) < 0 || Number(feePct) > 20) {
    throw new TypeError(`${label}.fee_pct must be between 0 and 20`);
  }
  const minUsd = nullableUsdString(item.min_usd, `${label}.min_usd`);
  const maxUsd = nullableUsdString(item.max_usd, `${label}.max_usd`);
  if (minUsd !== null && maxUsd !== null && Number(minUsd) > Number(maxUsd)) {
    throw new TypeError(`${label}.min_usd must not exceed max_usd`);
  }
  const title = nullableNonemptyString(item.title, `${label}.title`);
  if (title !== null && (title.length > 160 || /[\t\r\n]/.test(title))) {
    throw new TypeError(`${label}.title has an invalid format`);
  }
  const watch: DealWatchHavenoWatch = {
    id: positiveInteger(item.id, `${label}.id`),
    network: oneOf(item.network, ['reto'] as const, `${label}.network`),
    currency: decimalCode(item.currency, /^[A-Z]{3}$/, `${label}.currency`),
    paymentMethod: decimalCode(
      item.payment_method,
      /^[A-Z][A-Z0-9_]{1,39}$/,
      `${label}.payment_method`,
    ),
    thresholdPct,
    feePct,
    minUsd,
    maxUsd,
    title,
    displayTitle: nonemptyString(item.display_title, `${label}.display_title`),
    marketUrl: decodeExternalHttpUrl(item.market_url, `${label}.market_url`),
    createdAt: nonnegativeInteger(item.created_at, `${label}.created_at`),
    updatedAt: nonnegativeInteger(item.updated_at, `${label}.updated_at`),
    lastCheckedAt: nullableTimestamp(item.last_checked_at, `${label}.last_checked_at`),
    lastStatus: oneOf(item.last_status, CHECK_STATES, `${label}.last_status`),
    lastError: nullableNonemptyString(item.last_error, `${label}.last_error`),
    notifyMutedUntil: nullableTimestamp(item.notify_muted_until, `${label}.notify_muted_until`),
    notificationsMuted: booleanValue(item.notifications_muted, `${label}.notifications_muted`),
    referencePrice: nullablePriceString(item.reference_price, `${label}.reference_price`),
    referenceSource: decodeReferenceSource(item.reference_source, `${label}.reference_source`),
    referenceAt: nullableTimestamp(item.reference_at, `${label}.reference_at`),
    bestPrice: nullablePriceString(item.best_price, `${label}.best_price`),
    bestVsMarketPct: nullablePctString(item.best_vs_market_pct, `${label}.best_vs_market_pct`),
    bestVsMarket24hPct: nullablePctString(
      item.best_vs_market_24h_pct,
      `${label}.best_vs_market_24h_pct`,
    ),
    offerCount: nonnegativeInteger(item.offer_count, `${label}.offer_count`),
    matchCount: nonnegativeInteger(item.match_count, `${label}.match_count`),
    offers: arrayValue(item.offers, `${label}.offers`).map((offer, offerIndex) =>
      decodeOffer(offer, offerIndex, label),
    ),
  };
  validateWatchState(watch, label);
  validateOffers(watch, label);
  return watch;
}

function decimalCode(value: unknown, pattern: RegExp, label: string): string {
  const text = stringValue(value, label);
  if (!pattern.test(text)) throw new TypeError(`${label} has an invalid format`);
  return text;
}

function decodeSummary(value: unknown): DealWatchHavenoSummary {
  const summary = objectValue(value, 'price checks.haveno_summary');
  return {
    count: nonnegativeInteger(summary.count, 'price checks.haveno_summary.count'),
    checked: nonnegativeInteger(summary.checked, 'price checks.haveno_summary.checked'),
    matching: nonnegativeInteger(summary.matching, 'price checks.haveno_summary.matching'),
    errors: nonnegativeInteger(summary.errors, 'price checks.haveno_summary.errors'),
  };
}

function summariesMatch(
  returned: DealWatchHavenoSummary,
  observed: DealWatchHavenoSummary,
): boolean {
  return (
    returned.count === observed.count &&
    returned.checked === observed.checked &&
    returned.matching === observed.matching &&
    returned.errors === observed.errors
  );
}

export function decodeDealWatchHaveno(response: Record<string, unknown>): {
  havenoWatches: DealWatchHavenoWatch[];
  havenoSummary: DealWatchHavenoSummary;
} {
  const hasWatches = Object.prototype.hasOwnProperty.call(response, 'haveno_watches');
  const hasSummary = Object.prototype.hasOwnProperty.call(response, 'haveno_summary');
  if (!hasWatches && !hasSummary) {
    return { havenoWatches: [], havenoSummary: { count: 0, checked: 0, matching: 0, errors: 0 } };
  }
  if (hasWatches !== hasSummary) {
    throw new TypeError('price checks.haveno_watches and haveno_summary must appear together');
  }
  const havenoWatches = arrayValue(response.haveno_watches, 'price checks.haveno_watches').map(
    decodeWatch,
  );
  if (new Set(havenoWatches.map((watch) => watch.id)).size !== havenoWatches.length) {
    throw new TypeError('price checks.haveno_watches contains duplicate IDs');
  }
  const havenoSummary = decodeSummary(response.haveno_summary);
  const observed: DealWatchHavenoSummary = {
    count: havenoWatches.length,
    checked: havenoWatches.filter((watch) => watch.lastCheckedAt !== null).length,
    matching: havenoWatches.filter((watch) => watch.matchCount > 0).length,
    errors: havenoWatches.filter((watch) => watch.lastStatus === 'error').length,
  };
  if (!summariesMatch(havenoSummary, observed)) {
    throw new TypeError('price checks.haveno_summary does not match haveno_watches');
  }
  return { havenoWatches, havenoSummary };
}
