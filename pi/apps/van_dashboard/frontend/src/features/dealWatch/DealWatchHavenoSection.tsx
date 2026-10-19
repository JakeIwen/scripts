import { useEffect, useState } from 'react';

import { StatusPill } from '../../components/StatusPill';
import { useToast } from '../../components/ToastProvider';
import type { DealWatchControls } from './controls';
import { DealWatchHavenoActivity } from './DealWatchHavenoActivity';
import { DealWatchHavenoForm } from './DealWatchHavenoForm';
import { ExternalDealLink } from './ExternalDealLink';
import { promptForMuteDays } from './mutePrompt';
import {
  checkAge,
  formatUnixDateTime,
  havenoPercentLabel,
  havenoPriceLabel as priceLabel,
  havenoReferenceSourceLabel,
  havenoState,
} from './presentation';
import type {
  DealWatchHavenoActivity as DealWatchHavenoActivityData,
  DealWatchHavenoOffer,
  DealWatchHavenoWatch,
} from './types';

export interface DealWatchHavenoSectionProps {
  watches: DealWatchHavenoWatch[];
  activity: DealWatchHavenoActivityData | null;
  disabled: boolean;
  controls: DealWatchControls;
}

function xmrRange(offer: DealWatchHavenoOffer): string {
  if (offer.minXmr === null && offer.maxXmr === null) return 'XMR amount unavailable';
  if (offer.minXmr === null) return `Up to ${offer.maxXmr} XMR`;
  if (offer.maxXmr === null) return `From ${offer.minXmr} XMR`;
  return offer.minXmr === offer.maxXmr
    ? `${offer.minXmr} XMR`
    : `${offer.minXmr}–${offer.maxXmr} XMR`;
}

function HavenoOfferRow({
  offer,
  currency,
  showEffective,
}: {
  offer: DealWatchHavenoOffer;
  currency: string;
  showEffective: boolean;
}) {
  return (
    <li className="deal-watch-haveno-offer">
      <div className="deal-watch-haveno-offer__price">
        <strong>{priceLabel(offer.price, currency)}</strong>
        <span>{havenoPercentLabel(offer.vsMarketPct)} vs market</span>
        {showEffective && <span>{havenoPercentLabel(offer.effectivePct)} effective</span>}
        {offer.matches && (
          <StatusPill tone="good" dot={false}>
            Match
          </StatusPill>
        )}
      </div>
      <small>
        {xmrRange(offer)} · Listed {checkAge(offer.offeredAt)}
      </small>
    </li>
  );
}

function marketPrice(watch: DealWatchHavenoWatch): string {
  if (watch.referencePrice === null) return '—';
  return `${priceLabel(watch.referencePrice, watch.currency)} · ${havenoReferenceSourceLabel(watch.referenceSource)}`;
}

function bestOffer(watch: DealWatchHavenoWatch): string {
  if (watch.bestPrice === null || watch.bestVsMarketPct === null) return 'No offers';
  return `${priceLabel(watch.bestPrice, watch.currency)} · ${havenoPercentLabel(watch.bestVsMarketPct)}`;
}

interface HavenoCardProps {
  watch: DealWatchHavenoWatch;
  disabled: boolean;
  onEdit: () => void;
  onCheck: () => void;
  onMute: () => void;
  onRemove: () => void;
}

function HavenoCard({ watch, disabled, onEdit, onCheck, onMute, onRemove }: HavenoCardProps) {
  const state = havenoState(watch);
  return (
    <article className={`deal-watch-card deal-watch-card--${state.tone}`}>
      <header className="deal-watch-card__header">
        <div>
          <ExternalDealLink url={watch.marketUrl}>{watch.displayTitle}</ExternalDealLink>
          <small>
            {watch.paymentMethod} · {watch.currency} · {watch.network}
          </small>
        </div>
        <StatusPill tone={state.tone}>{state.label}</StatusPill>
      </header>
      <dl className="deal-watch-card__facts deal-watch-haveno-card__facts">
        <div>
          <dt>Market price</dt>
          <dd>{marketPrice(watch)}</dd>
        </div>
        <div>
          <dt>Best offer</dt>
          <dd>{bestOffer(watch)}</dd>
        </div>
        <div>
          <dt>Best 24h vs market</dt>
          <dd>
            {watch.bestVsMarket24hPct === null ? '—' : havenoPercentLabel(watch.bestVsMarket24hPct)}
          </dd>
        </div>
        <div>
          <dt>Alert at</dt>
          <dd>
            ≤ {havenoPercentLabel(watch.thresholdPct)} vs market
            {Number(watch.feePct) > 0 && ` · incl. ${havenoPercentLabel(watch.feePct)} fee`}
          </dd>
        </div>
        {Number(watch.feePct) > 0 && (
          <div>
            <dt>Fee</dt>
            <dd>{havenoPercentLabel(watch.feePct)}</dd>
          </div>
        )}
        <div>
          <dt>Matching</dt>
          <dd>
            {watch.matchCount} of {watch.offerCount}
          </dd>
        </div>
        <div>
          <dt>Last check</dt>
          <dd>{checkAge(watch.lastCheckedAt)}</dd>
        </div>
        <div>
          <dt>Notifications</dt>
          <dd>
            {watch.notificationsMuted
              ? `Muted until ${formatUnixDateTime(watch.notifyMutedUntil)}`
              : 'Enabled'}
          </dd>
        </div>
      </dl>
      {watch.lastError && <p className="deal-watch-card__error">{watch.lastError}</p>}
      <div className="deal-watch-card__actions">
        <button type="button" disabled={disabled} onClick={onCheck}>
          Check
        </button>
        <button type="button" disabled={disabled} onClick={onEdit}>
          Edit
        </button>
        <button type="button" disabled={disabled} onClick={onMute}>
          {watch.notificationsMuted ? 'Unmute' : 'Mute…'}
        </button>
        <button type="button" className="danger-button" disabled={disabled} onClick={onRemove}>
          Remove
        </button>
      </div>
      {watch.offers.length ? (
        <ul className="deal-watch-haveno-offers">
          {watch.offers.map((offer) => (
            <HavenoOfferRow
              offer={offer}
              currency={watch.currency}
              showEffective={Number(watch.feePct) > 0}
              key={offer.offerId}
            />
          ))}
        </ul>
      ) : (
        <p className="deal-watch-card__empty">No current offers for this watch.</p>
      )}
    </article>
  );
}

export function DealWatchHavenoSection({
  watches,
  activity,
  disabled,
  controls,
}: DealWatchHavenoSectionProps) {
  const { showToast } = useToast();
  const [editingId, setEditingId] = useState<number | null>(null);
  const editing = watches.find((watch) => watch.id === editingId) ?? null;

  useEffect(() => {
    if (editingId !== null && editing === null) setEditingId(null);
  }, [editing, editingId]);

  const mute = (watch: DealWatchHavenoWatch) => {
    if (watch.notificationsMuted) {
      void controls.setHavenoMute(watch.id, 0);
      return;
    }
    const days = promptForMuteDays(watch.displayTitle, showToast);
    if (days !== null) void controls.setHavenoMute(watch.id, days);
  };

  return (
    <section className="deal-watch-section" aria-labelledby="deal-watch-haveno-title">
      <header className="deal-watch-section__header">
        <div>
          <h3 id="deal-watch-haveno-title">Haveno XMR offers</h3>
          <p>Offers to buy XMR compared with the Haveno market price.</p>
        </div>
        <span>{watches.length}</span>
      </header>
      <div className="deal-watch-card-list">
        {watches.length ? (
          watches.map((watch) => (
            <HavenoCard
              watch={watch}
              disabled={disabled}
              onEdit={() => setEditingId(watch.id)}
              onCheck={() => void controls.checkHaveno(watch.id)}
              onMute={() => mute(watch)}
              onRemove={() => {
                if (window.confirm(`Remove ${watch.displayTitle}?`)) {
                  void controls.removeHavenoWatch(watch.id);
                }
              }}
              key={watch.id}
            />
          ))
        ) : (
          <p className="deal-watch-section__empty">No Haveno offer watches configured.</p>
        )}
      </div>
      <DealWatchHavenoForm
        editing={editing}
        disabled={disabled}
        onCancel={() => setEditingId(null)}
        onSubmit={async (draft) => {
          const saved = editing
            ? await controls.editHavenoWatch(editing.id, draft)
            : await controls.addHavenoWatch(draft);
          if (saved) setEditingId(null);
          return saved;
        }}
      />
      <DealWatchHavenoActivity activity={activity} />
    </section>
  );
}
