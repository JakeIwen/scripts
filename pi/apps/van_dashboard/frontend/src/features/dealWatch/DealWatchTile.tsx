import type { PollingState } from '../../hooks/usePollingResource';
import { KeyValueList } from '../../components/KeyValueList';
import { StatusPill } from '../../components/StatusPill';
import { Tile } from '../../components/Tile';
import {
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

function tileStatusLabel(status: DealWatchStatus | null, _error: Error | null): string {
  if (!status) return '—';
  return String(watchedCount(status));
}

function compactAge(timestamp: number | null): string {
  if (timestamp === null) return 'never';
  const seconds = Math.max(0, Date.now() / 1_000 - timestamp);
  return seconds < 90 ? `${Math.round(seconds)}s ago` : `${Math.round(seconds / 60)}m ago`;
}

export function DealWatchTile({ resource, onOpen }: DealWatchTileProps) {
  const status = resource.data;
  const tone = dealWatchTone(status, resource.error);
  const latestListing = status ? mostRecentPricedListing(status) : null;
  const summary = status
    ? watchedCount(status) === 0
      ? 'Nothing watched'
      : `${status.listingSummary.count} listings · ${status.searchSummary.count} queries · ${status.listingSummary.errors + status.searchSummary.errors} errors`
    : resource.error
      ? 'Price data unavailable'
      : 'Loading watched items…';
  const details = [
    {
      label: 'Latest price',
      value: latestListing
        ? `$${latestListing.lastPrice} · ${latestListing.displayTitle}`
        : resource.error && !status
          ? 'Unavailable'
          : '—',
    },
    {
      label: 'Last check',
      value: status ? compactAge(mostRecentCheckAt(status)) : 'never',
    },
  ];

  return (
    <Tile
      icon="🏷️"
      title="Deal Watch"
      summary={summary}
      status={
        <StatusPill tone={tone} dot={false}>
          {tileStatusLabel(status, resource.error)}
        </StatusPill>
      }
      tone={tone === 'good' || tone === 'bad' ? tone : 'neutral'}
      onClick={onOpen}
      ariaLabel="Open Deal Watch details"
      className="deal-watch-tile"
    >
      <KeyValueList items={details} className="deal-watch-tile__details" />
    </Tile>
  );
}
