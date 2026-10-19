import {
  arrayValue,
  nullableNumber,
  nullableString,
  numberValue,
  objectValue,
  stringValue,
} from '../../api/validation';
import {
  HAVENO_ACTIVITY_CONFIDENCES,
  HAVENO_ACTIVITY_OUTCOMES,
  type DealWatchHavenoActivity,
  type DealWatchHavenoActivityConfidence,
  type DealWatchHavenoActivityOffer,
  type DealWatchHavenoActivityOutcome,
  type DealWatchHavenoActivityWindow,
} from './types';

const PRICE_PATTERN = /^\d+\.\d{2}$/;
const DECIMAL_PATTERN = /^(?:0|[1-9]\d*(?:\.\d*[1-9])?|0\.\d*[1-9])$/;
const HOURS_PATTERN = /^\d+\.\d$/;

function oneOf<T extends string>(value: unknown, choices: readonly T[], label: string): T {
  const text = stringValue(value, label);
  if (!choices.includes(text as T)) {
    throw new TypeError(`${label} has an unsupported value: ${text}`);
  }
  return text as T;
}

function exactValue<T extends string>(value: unknown, expected: T, label: string): T {
  const text = stringValue(value, label);
  if (text !== expected) throw new TypeError(`${label} must be ${expected}`);
  return expected;
}

function nonemptyString(value: unknown, label: string): string {
  const text = stringValue(value, label);
  if (!text.trim()) throw new TypeError(`${label} must not be empty`);
  return text;
}

function nullableNonemptyString(value: unknown, label: string): string | null {
  const text = nullableString(value, label);
  if (text !== null && !text.trim()) throw new TypeError(`${label} must not be empty`);
  return text;
}

function nonnegativeInteger(value: unknown, label: string): number {
  const number = numberValue(value, label);
  if (!Number.isSafeInteger(number) || number < 0) {
    throw new TypeError(`${label} must be a non-negative safe integer`);
  }
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

function xmrString(value: unknown, label: string, allowZero: boolean): string {
  const text = decimalString(value, DECIMAL_PATTERN, label);
  if (!allowZero && Number(text) <= 0) throw new TypeError(`${label} must be greater than zero`);
  return text;
}

function decodeWindow(value: unknown, index: number): DealWatchHavenoActivityWindow {
  const label = `price checks.haveno_activity.windows[${index}]`;
  const window = objectValue(value, label);
  const days = nonnegativeInteger(window.days, `${label}.days`);
  if (days !== 7 && days !== 30) throw new TypeError(`${label}.days must be 7 or 30`);
  const decoded: DealWatchHavenoActivityWindow = {
    days,
    listed: nonnegativeInteger(window.listed, `${label}.listed`),
    taken: nonnegativeInteger(window.taken, `${label}.taken`),
    takenHigh: nonnegativeInteger(window.taken_high, `${label}.taken_high`),
    withdrawn: nonnegativeInteger(window.withdrawn, `${label}.withdrawn`),
    pending: nonnegativeInteger(window.pending, `${label}.pending`),
    takenXmr: xmrString(window.taken_xmr, `${label}.taken_xmr`, true),
    medianHoursToTake:
      window.median_hours_to_take === null
        ? null
        : decimalString(
            window.median_hours_to_take,
            HOURS_PATTERN,
            `${label}.median_hours_to_take`,
          ),
    marketTrades: nonnegativeInteger(window.market_trades, `${label}.market_trades`),
    marketTradesXmr: xmrString(window.market_trades_xmr, `${label}.market_trades_xmr`, true),
  };
  if (decoded.takenHigh > decoded.taken) {
    throw new TypeError(`${label}.taken_high must not exceed taken`);
  }
  return decoded;
}

function decodeOffer(value: unknown, index: number): DealWatchHavenoActivityOffer {
  const label = `price checks.haveno_activity.recent[${index}]`;
  const offer = objectValue(value, label);
  const minXmr = xmrString(offer.min_xmr, `${label}.min_xmr`, false);
  const maxXmr = xmrString(offer.max_xmr, `${label}.max_xmr`, false);
  const decoded: DealWatchHavenoActivityOffer = {
    offerId: nonemptyString(offer.offer_id, `${label}.offer_id`),
    price: priceString(offer.price, `${label}.price`),
    minXmr,
    maxXmr,
    offeredAt: nonnegativeInteger(offer.offered_at, `${label}.offered_at`),
    firstSeenAt: nonnegativeInteger(offer.first_seen_at, `${label}.first_seen_at`),
    goneAt: nonnegativeInteger(offer.gone_at, `${label}.gone_at`),
    outcome: oneOf(
      offer.outcome,
      HAVENO_ACTIVITY_OUTCOMES,
      `${label}.outcome`,
    ) as DealWatchHavenoActivityOutcome,
    confidence:
      offer.confidence === null
        ? null
        : (oneOf(
            offer.confidence,
            HAVENO_ACTIVITY_CONFIDENCES,
            `${label}.confidence`,
          ) as DealWatchHavenoActivityConfidence),
    tradePrice:
      offer.trade_price === null ? null : priceString(offer.trade_price, `${label}.trade_price`),
    tradeXmr:
      offer.trade_xmr === null ? null : xmrString(offer.trade_xmr, `${label}.trade_xmr`, false),
    tradeAt: nullableTimestamp(offer.trade_at, `${label}.trade_at`),
  };
  validateOffer(decoded, label);
  return decoded;
}

function validateOffer(offer: DealWatchHavenoActivityOffer, label: string): void {
  if (Number(offer.minXmr) > Number(offer.maxXmr)) {
    throw new TypeError(`${label}.min_xmr must not exceed max_xmr`);
  }
  if (offer.offeredAt > offer.goneAt || offer.firstSeenAt > offer.goneAt) {
    throw new TypeError(`${label} has timestamps after gone_at`);
  }
  const tradeFields = [offer.tradePrice, offer.tradeXmr, offer.tradeAt];
  if (offer.outcome === 'taken') {
    if (offer.confidence === null || tradeFields.some((field) => field === null)) {
      throw new TypeError(`${label} taken outcome requires confidence and trade details`);
    }
  } else if (offer.confidence !== null || tradeFields.some((field) => field !== null)) {
    throw new TypeError(`${label} non-taken outcome must not include trade details`);
  }
}

function decodeWindows(
  value: unknown,
): [DealWatchHavenoActivityWindow, DealWatchHavenoActivityWindow] {
  const windows = arrayValue(value, 'price checks.haveno_activity.windows').map(decodeWindow);
  if (windows.length !== 2 || windows[0]?.days !== 7 || windows[1]?.days !== 30) {
    throw new TypeError('price checks.haveno_activity.windows must contain 7 and 30 days in order');
  }
  return [windows[0], windows[1]];
}

function decodeRecent(value: unknown): DealWatchHavenoActivityOffer[] {
  const recent = arrayValue(value, 'price checks.haveno_activity.recent').map(decodeOffer);
  if (recent.length > 15)
    throw new TypeError('price checks.haveno_activity.recent exceeds 15 offers');
  if (new Set(recent.map((offer) => offer.offerId)).size !== recent.length) {
    throw new TypeError('price checks.haveno_activity.recent contains duplicate offer IDs');
  }
  for (let index = 1; index < recent.length; index += 1) {
    if (recent[index - 1]!.goneAt < recent[index]!.goneAt) {
      throw new TypeError('price checks.haveno_activity.recent must be newest first');
    }
  }
  return recent;
}

function validateSnapshotState(activity: DealWatchHavenoActivity): void {
  if ((activity.trackingSince === null) !== (activity.lastSnapshotAt === null)) {
    throw new TypeError('price checks.haveno_activity has incomplete snapshot timestamps');
  }
  if (
    activity.trackingSince !== null &&
    activity.lastSnapshotAt !== null &&
    activity.trackingSince > activity.lastSnapshotAt
  ) {
    throw new TypeError('price checks.haveno_activity.tracking_since follows last_snapshot_at');
  }
  if (activity.trackingSince !== null) return;
  const hasCounts = activity.windows.some(
    (window) =>
      window.listed +
        window.taken +
        window.takenHigh +
        window.withdrawn +
        window.pending +
        window.marketTrades >
        0 ||
      Number(window.takenXmr) > 0 ||
      Number(window.marketTradesXmr) > 0 ||
      window.medianHoursToTake !== null,
  );
  if (activity.currentOffers !== 0 || activity.recent.length > 0 || hasCounts) {
    throw new TypeError('price checks.haveno_activity has data before its first snapshot');
  }
}

export function decodeDealWatchHavenoActivity(
  response: Record<string, unknown>,
): DealWatchHavenoActivity | null {
  if (!Object.prototype.hasOwnProperty.call(response, 'haveno_activity')) return null;
  const label = 'price checks.haveno_activity';
  const value = objectValue(response.haveno_activity, label);
  const activity: DealWatchHavenoActivity = {
    network: exactValue(value.network, 'reto', `${label}.network`),
    currency: exactValue(value.currency, 'USD', `${label}.currency`),
    paymentMethod: exactValue(value.payment_method, 'ZELLE', `${label}.payment_method`),
    trackingSince: nullableTimestamp(value.tracking_since, `${label}.tracking_since`),
    lastSnapshotAt: nullableTimestamp(value.last_snapshot_at, `${label}.last_snapshot_at`),
    lastError: nullableNonemptyString(value.last_error, `${label}.last_error`),
    currentOffers: nonnegativeInteger(value.current_offers, `${label}.current_offers`),
    windows: decodeWindows(value.windows),
    recent: decodeRecent(value.recent),
  };
  validateSnapshotState(activity);
  return activity;
}
