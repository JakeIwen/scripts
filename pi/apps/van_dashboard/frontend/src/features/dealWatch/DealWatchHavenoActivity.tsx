import { StatusPill, type StatusTone } from '../../components/StatusPill';
import { formatDuration } from '../../utils/format';
import { checkAge, formatUnixDateTime, havenoPriceLabel } from './presentation';
import type {
  DealWatchHavenoActivity,
  DealWatchHavenoActivityOffer,
  DealWatchHavenoActivityWindow,
} from './types';

function summaryFigure(activity: DealWatchHavenoActivity): string {
  if (activity.trackingSince === null) return 'Not collected yet';
  const week = activity.windows[0];
  return `${week.taken} taken · ${week.withdrawn} withdrawn (7d)`;
}

function xmrRange(offer: DealWatchHavenoActivityOffer): string {
  return offer.minXmr === offer.maxXmr
    ? `${offer.minXmr} XMR`
    : `${offer.minXmr}–${offer.maxXmr} XMR`;
}

function outcomePresentation(offer: DealWatchHavenoActivityOffer): {
  label: string;
  tone: StatusTone;
} {
  if (offer.outcome === 'taken' && offer.confidence === 'high') {
    return { label: 'Taken', tone: 'good' };
  }
  if (offer.outcome === 'taken') return { label: 'Taken?', tone: 'warning' };
  if (offer.outcome === 'withdrawn') return { label: 'Withdrawn', tone: 'neutral' };
  return { label: 'Pending', tone: 'warning' };
}

function ActivityWindow({ window }: { window: DealWatchHavenoActivityWindow }) {
  return (
    <article className="deal-watch-activity-window">
      <h5>{window.days} days</h5>
      <dl>
        <div>
          <dt>Listed</dt>
          <dd>{window.listed}</dd>
        </div>
        <div>
          <dt>Taken</dt>
          <dd>
            {window.taken} ({window.takenHigh} sure)
          </dd>
        </div>
        <div>
          <dt>Withdrawn</dt>
          <dd>{window.withdrawn}</dd>
        </div>
        <div>
          <dt>Pending</dt>
          <dd>{window.pending}</dd>
        </div>
        <div>
          <dt>Median to take</dt>
          <dd>{window.medianHoursToTake === null ? '—' : `${window.medianHoursToTake} h`}</dd>
        </div>
        <div>
          <dt>Zelle trades</dt>
          <dd>
            {window.marketTrades} · {window.marketTradesXmr} XMR
          </dd>
        </div>
      </dl>
    </article>
  );
}

function RecentOffer({
  offer,
  currency,
}: {
  offer: DealWatchHavenoActivityOffer;
  currency: string;
}) {
  const outcome = outcomePresentation(offer);
  return (
    <li className="deal-watch-activity-offer">
      <div className="deal-watch-activity-offer__heading">
        <div>
          <strong>{havenoPriceLabel(offer.price, currency)}</strong>
          <span>{xmrRange(offer)}</span>
        </div>
        <StatusPill tone={outcome.tone} dot={false}>
          {outcome.label}
        </StatusPill>
      </div>
      <small>Listed for {formatDuration(offer.goneAt - offer.offeredAt)}</small>
      {offer.outcome === 'taken' && offer.tradePrice !== null && offer.tradeXmr !== null && (
        <small>
          Matched trade {havenoPriceLabel(offer.tradePrice, currency)} · {offer.tradeXmr} XMR
        </small>
      )}
    </li>
  );
}

export function DealWatchHavenoActivity({
  activity,
}: {
  activity: DealWatchHavenoActivity | null;
}) {
  if (activity === null) return null;
  return (
    <details className="deal-watch-activity">
      <summary className="deal-watch-activity__summary">
        <span className="deal-watch-activity__chevron" aria-hidden="true">
          ▸
        </span>
        <h4>Zelle offer activity</h4>
        <span>{summaryFigure(activity)}</span>
      </summary>
      <div className="deal-watch-activity__body">
        <dl className="deal-watch-activity__metadata">
          <div>
            <dt>Tracking since</dt>
            <dd>{formatUnixDateTime(activity.trackingSince)}</dd>
          </div>
          <div>
            <dt>Last snapshot</dt>
            <dd>{checkAge(activity.lastSnapshotAt)}</dd>
          </div>
          <div>
            <dt>Current offers</dt>
            <dd>{activity.currentOffers}</dd>
          </div>
        </dl>
        {activity.lastError && <p className="deal-watch-card__error">{activity.lastError}</p>}
        <div className="deal-watch-activity__windows">
          {activity.windows.map((window) => (
            <ActivityWindow window={window} key={window.days} />
          ))}
        </div>
        <div className="deal-watch-activity__recent">
          <h5>Recent vanished offers</h5>
          {activity.recent.length ? (
            <ul>
              {activity.recent.map((offer) => (
                <RecentOffer offer={offer} currency={activity.currency} key={offer.offerId} />
              ))}
            </ul>
          ) : (
            <p>No vanished offers recorded.</p>
          )}
        </div>
        <p className="deal-watch-activity__note">
          Inferred by matching vanished offers to public trades; trade data has no offer IDs.
        </p>
      </div>
    </details>
  );
}
