import type { PollingState } from '../../hooks/usePollingResource';
import { KeyValueList } from '../../components/KeyValueList';
import { StatusPill } from '../../components/StatusPill';
import { Tile } from '../../components/Tile';
import {
  checkAge,
  dealWatchTone,
  mostRecentCheckAt,
  mostRecentPricedListing,
  watchedCount,
} from './presentation';
import type { DealWatchStatus } from './types';
import './dealWatch.css';

export interface DealWatchTileProps {
  resource: PollingState<DealWatchStatus>;
  operationBusy?: boolean;
  onOpen: () => void;
}

function tileStatusLabel(status: DealWatchStatus | null, error: Error | null): string {
  if (!status) return error ? 'No data' : 'Loading';
  const errors = status.listingSummary.errors + status.searchSummary.errors;
  if (errors) return `${errors} ${errors === 1 ? 'error' : 'errors'}`;
  if (status.listingSummary.belowThreshold) {
    return `${status.listingSummary.belowThreshold} below target`;
  }
  return `${watchedCount(status)} watched`;
}

export function DealWatchTile({ resource, operationBusy = false, onOpen }: DealWatchTileProps) {
  const status = resource.data;
  const tone = dealWatchTone(status, resource.error);
  const latestListing = status ? mostRecentPricedListing(status) : null;
  const summary = status
    ? watchedCount(status) === 0
      ? 'Nothing watched'
      : `${status.listingSummary.count} ${
          status.listingSummary.count === 1 ? 'listing' : 'listings'
        } · ${status.searchSummary.count} ${
          status.searchSummary.count === 1 ? 'search' : 'searches'
        }`
    : (resource.error?.message ?? 'Loading watched listings and searches…');
  const details = [
    {
      label: 'Latest price',
      value: latestListing ? `$${latestListing.lastPrice} · ${latestListing.displayTitle}` : '—',
    },
    {
      label: 'Last check',
      value: status ? checkAge(mostRecentCheckAt(status)) : '—',
    },
    {
      label: 'Schedule',
      value: status?.schedule.error
        ? 'Description unavailable'
        : (status?.schedule.description ?? '—'),
    },
  ];

  return (
    <Tile
      icon="🏷️"
      title="Deal Watch"
      summary={summary}
      status={<StatusPill tone={tone}>{tileStatusLabel(status, resource.error)}</StatusPill>}
      tone={tone}
      onClick={onOpen}
      ariaLabel="Open Deal Watch details"
      className="deal-watch-tile"
    >
      <KeyValueList items={details} className="deal-watch-tile__details" />
      {resource.error && status && (
        <p className="deal-watch-stale-error">Refresh failed · {resource.error.message}</p>
      )}
      <p className="deal-watch-read-only">
        {operationBusy
          ? 'Deal Watch operation in progress…'
          : resource.refreshing
            ? 'Refreshing Deal Watch…'
            : 'Open details to manage watches'}
      </p>
    </Tile>
  );
}
