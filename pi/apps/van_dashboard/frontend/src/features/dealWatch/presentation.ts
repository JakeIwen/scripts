import type { StatusTone } from '../../components/StatusPill';
import { formatRelativeTime } from '../../utils/format';
import type { DealWatchListing, DealWatchSavedSearch, DealWatchStatus } from './types';

export function dealWatchTone(status: DealWatchStatus | null, error: Error | null): StatusTone {
  if (!status) return error ? 'bad' : 'neutral';
  if (status.listingSummary.errors + status.searchSummary.errors > 0) return 'bad';
  if (status.schedule.error || error) return 'warning';
  if (status.listingSummary.belowThreshold > 0) return 'good';
  return 'neutral';
}

export function watchedCount(status: DealWatchStatus): number {
  return status.listingSummary.count + status.searchSummary.count;
}

export function mostRecentCheckAt(status: DealWatchStatus): number | null {
  const timestamps = [...status.listings, ...status.searches]
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
