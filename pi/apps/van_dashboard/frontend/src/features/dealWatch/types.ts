export type DealWatchCheckState = 'never' | 'ok' | 'error';

export interface DealWatchListingSummary {
  count: number;
  checked: number;
  belowThreshold: number;
  errors: number;
}

export interface DealWatchListing {
  id: number;
  parser: 'amazon';
  threshold: string;
  thresholdCents: number;
  url: string;
  title: string | null;
  displayTitle: string;
  createdAt: number;
  updatedAt: number;
  lastCheckedAt: number | null;
  lastPrice: string | null;
  lastPriceCents: number | null;
  lastPriceCheckedAt: number | null;
  lastStatus: DealWatchCheckState;
  lastError: string | null;
  lastTitle: string | null;
  notifyMutedUntil: number | null;
  notificationsMuted: boolean;
  belowThreshold: boolean | null;
}

export interface DealWatchSearchResult {
  itemId: string;
  title: string;
  url: string;
  price: string | null;
  shipping: string | null;
  imageUrl: string | null;
  firstSeenAt: number;
  lastSeenAt: number;
  dismissed: false;
  dismissedAt: null;
}

export interface DealWatchSavedSearch {
  id: number;
  parser: 'ebay';
  url: string;
  title: string | null;
  displayTitle: string;
  createdAt: number;
  updatedAt: number;
  lastCheckedAt: number | null;
  lastStatus: DealWatchCheckState;
  lastError: string | null;
  resultCount: number;
  hiddenCurrentCount: number;
  dismissedCount: number;
  knownCount: number;
  results: DealWatchSearchResult[];
}

export interface DealWatchSearchSummary {
  count: number;
  checked: number;
  results: number;
  errors: number;
}

export type DealWatchScheduleErrorCode = 'parse' | 'rate_limit';

export interface DealWatchSchedule {
  expression: string;
  description: string;
  error: string | null;
  errorCode: DealWatchScheduleErrorCode | null;
}

export interface DealWatchStatus {
  listings: DealWatchListing[];
  listingSummary: DealWatchListingSummary;
  searches: DealWatchSavedSearch[];
  searchSummary: DealWatchSearchSummary;
  schedule: DealWatchSchedule;
}

export interface DealWatchListingDraft {
  parser: 'amazon';
  threshold: string;
  url: string;
  title: string;
}

export interface DealWatchSearchDraft {
  parser: 'ebay';
  url: string;
  title: string;
}

export interface DealWatchMutationResult {
  message: string;
}
