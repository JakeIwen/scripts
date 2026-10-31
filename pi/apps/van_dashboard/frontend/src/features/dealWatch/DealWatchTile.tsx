import type { PollingState } from '../../hooks/usePollingResource';
import { KeyValueList } from '../../components/KeyValueList';
import { StatusPill } from '../../components/StatusPill';
import { Tile } from '../../components/Tile';
import {
  bestCurrentHavenoOffer,
  dealWatchTone,
  havenoPercentLabel,
  havenoPriceLabel,
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

function countLabel(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`;
}

export function DealWatchTile({ resource, onOpen }: DealWatchTileProps) {
  const status = resource.data;
  const tone = dealWatchTone(status, resource.error);
  const latestListing = status ? mostRecentPricedListing(status) : null;
  const bestHaveno = status ? bestCurrentHavenoOffer(status) : null;
  const summary = status
    ? watchedCount(status) === 0
      ? 'Nothing watched'
      : `${countLabel(status.listingSummary.count, 'listing')} · ${countLabel(status.searchSummary.count, 'query', 'queries')} · ${status.havenoSummary.count} XMR · ${countLabel(status.listingSummary.errors + status.searchSummary.errors + status.havenoSummary.errors, 'error')}`
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
      label: 'XMR',
      value: bestHaveno
        ? `${havenoPriceLabel(bestHaveno.bestPrice!, bestHaveno.currency)} · ${havenoPercentLabel(bestHaveno.bestVsMarketPct!)}`
        : status?.havenoSummary.count
          ? 'No offers'
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
        <StatusPill tone={tone} dot={false} detail={summary} label="Deal Watch status">
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
