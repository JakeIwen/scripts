import type { StatusTone } from '../../components/StatusPill';
import { formatRelativeTime } from '../../utils/format';
import type {
  DealWatchHavenoReferenceSource,
  DealWatchHavenoWatch,
  DealWatchListing,
  DealWatchSavedSearch,
  DealWatchStatus,
} from './types';

export function dealWatchTone(status: DealWatchStatus | null, _error: Error | null): StatusTone {
  if (!status) return 'neutral';
  if (
    status.listingSummary.errors + status.searchSummary.errors + status.havenoSummary.errors >
    0
  ) {
    return 'bad';
  }
  if (status.listingSummary.belowThreshold > 0 || status.havenoSummary.matching > 0) return 'good';
  return 'neutral';
}

export function watchedCount(status: DealWatchStatus): number {
  return status.listingSummary.count + status.searchSummary.count + status.havenoSummary.count;
}

export function mostRecentCheckAt(status: DealWatchStatus): number | null {
  const timestamps = [...status.listings, ...status.searches, ...status.havenoWatches]
    .map((item) => item.lastCheckedAt)
    .filter((timestamp): timestamp is number => timestamp !== null);
  return timestamps.length ? Math.max(...timestamps) : null;
}

export function mostRecentPricedListing(status: DealWatchStatus): DealWatchListing | null {
  return status.listings.reduce<DealWatchListing | null>((latest, item) => {
    if (item.lastPrice === null || item.lastPriceCheckedAt === null) return latest;
    if (latest?.lastPriceCheckedAt === null || latest?.lastPriceCheckedAt === undefined) {
      return item;
    }
    return item.lastPriceCheckedAt > latest.lastPriceCheckedAt ? item : latest;
  }, null);
}

export function bestCurrentHavenoOffer(status: DealWatchStatus): DealWatchHavenoWatch | null {
  return status.havenoWatches.reduce<DealWatchHavenoWatch | null>((best, watch) => {
    if (watch.bestPrice === null || watch.bestVsMarketPct === null) return best;
    if (best?.bestVsMarketPct === null || best?.bestVsMarketPct === undefined) return watch;
    return Number(watch.bestVsMarketPct) < Number(best.bestVsMarketPct) ? watch : best;
  }, null);
}

export function havenoPriceLabel(price: string, currency: string): string {
  return currency === 'USD' ? `$${price}` : `${price} ${currency}`;
}

function havenoDollars(xmr: string, price: string): string {
  return `$${Math.round(Number(xmr) * Number(price)).toLocaleString('en-US')}`;
}

/** Offer size in dollars at its listed price, with the XMR amounts alongside. */
export function havenoSizeLabel(minXmr: string, maxXmr: string, price: string): string {
  if (minXmr === maxXmr) return `${havenoDollars(minXmr, price)} (${minXmr} XMR)`;
  return `${havenoDollars(minXmr, price)}–${havenoDollars(maxXmr, price)} (${minXmr}–${maxXmr} XMR)`;
}

export function havenoPercentLabel(value: string): string {
  const number = Number(value);
  const magnitude = Math.abs(number).toFixed(2);
  if (number > 0) return `+${magnitude}%`;
  if (number < 0) return `−${magnitude}%`;
  return '0.00%';
}

export function havenoReferenceSourceLabel(source: DealWatchHavenoReferenceSource | null): string {
  if (source === 'haveno-pricenode') return 'Haveno pricenode';
  if (source === 'kraken') return 'Kraken';
  return '—';
}

export function havenoState(watch: DealWatchHavenoWatch): {
  label: string;
  tone: StatusTone;
} {
  if (watch.lastStatus === 'error') return { label: 'Error', tone: 'bad' };
  if (watch.matchCount > 0) return { label: `${watch.matchCount} matching`, tone: 'good' };
  if (watch.lastStatus === 'never') return { label: 'Not checked', tone: 'neutral' };
  return { label: 'Checked', tone: 'neutral' };
}

export function listingState(item: DealWatchListing): {
  label: string;
  tone: StatusTone;
} {
  if (item.lastStatus === 'error') return { label: 'Error', tone: 'bad' };
  if (item.belowThreshold) return { label: 'Below target', tone: 'good' };
  if (item.lastStatus === 'never') return { label: 'Not checked', tone: 'neutral' };
  return { label: 'Checked', tone: 'neutral' };
}

export function searchState(search: DealWatchSavedSearch): {
  label: string;
  tone: StatusTone;
} {
  if (search.lastStatus === 'error') return { label: 'Error', tone: 'bad' };
  if (search.lastStatus === 'never') return { label: 'Not checked', tone: 'neutral' };
  return { label: `${search.resultCount} visible`, tone: 'neutral' };
}

export function checkAge(timestamp: number | null): string {
  return timestamp === null ? 'Never' : formatRelativeTime(timestamp);
}

export function formatUnixDateTime(timestamp: number | null): string {
  if (timestamp === null) return '—';
  return new Date(timestamp * 1_000).toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}
