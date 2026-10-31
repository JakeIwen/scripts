import { useEffect, useState } from 'react';

import { BottomSheet } from '../../components/BottomSheet';
import { StatusPill } from '../../components/StatusPill';
import { useToast } from '../../components/ToastProvider';
import type { PollingState } from '../../hooks/usePollingResource';
import type { DealWatchControls } from './controls';
import { DealWatchHavenoSection } from './DealWatchHavenoSection';
import { DealWatchListingForm } from './DealWatchListingForm';
import { DealWatchScheduleForm } from './DealWatchScheduleForm';
import { DealWatchSearchSection } from './DealWatchSearchSection';
import { ExternalDealLink } from './ExternalDealLink';
import { promptForMuteDays } from './mutePrompt';
import { checkAge, formatUnixDateTime, listingState } from './presentation';
import type { DealWatchListing, DealWatchStatus } from './types';
import './dealWatch.css';

export interface DealWatchSheetProps {
  open: boolean;
  onClose: () => void;
  resource: PollingState<DealWatchStatus>;
  controls: DealWatchControls;
}

interface ListingCardProps {
  listing: DealWatchListing;
  disabled: boolean;
  onEdit: () => void;
  onCheck: () => void;
  onMute: () => void;
  onRemove: () => void;
}

function ListingCard({ listing, disabled, onEdit, onCheck, onMute, onRemove }: ListingCardProps) {
  const state = listingState(listing);
  return (
    <article className={`deal-watch-card deal-watch-card--${state.tone}`}>
      <header className="deal-watch-card__header">
        <div>
          <ExternalDealLink url={listing.url}>{listing.displayTitle}</ExternalDealLink>
          <small>{listing.parser} listing</small>
        </div>
        <StatusPill tone={state.tone}>{state.label}</StatusPill>
      </header>
      <dl className="deal-watch-card__facts">
        <div>
          <dt>Last price</dt>
          <dd>{listing.lastPrice === null ? '—' : `$${listing.lastPrice}`}</dd>
        </div>
        <div>
          <dt>Alert below</dt>
          <dd>${listing.threshold}</dd>
        </div>
        <div>
          <dt>Last check</dt>
          <dd>{checkAge(listing.lastCheckedAt)}</dd>
        </div>
        <div>
          <dt>Notifications</dt>
          <dd>
            {listing.notificationsMuted
              ? `Muted until ${formatUnixDateTime(listing.notifyMutedUntil)}`
              : 'Enabled'}
          </dd>
        </div>
      </dl>
      {listing.lastError && <p className="deal-watch-card__error">{listing.lastError}</p>}
      <div className="deal-watch-card__actions">
        <button type="button" disabled={disabled} onClick={onCheck}>
          Check
        </button>
        <button type="button" disabled={disabled} onClick={onEdit}>
          Edit
        </button>
        <button type="button" disabled={disabled} onClick={onMute}>
          {listing.notificationsMuted ? 'Unmute' : 'Mute…'}
        </button>
        <button type="button" className="danger-button" disabled={disabled} onClick={onRemove}>
          Remove
        </button>
      </div>
    </article>
  );
}

export function DealWatchSheet({ open, onClose, resource, controls }: DealWatchSheetProps) {
  const status = resource.data;
  const { showToast } = useToast();
  const [editingListingId, setEditingListingId] = useState<number | null>(null);
  const editingListing =
    status?.listings.find((listing) => listing.id === editingListingId) ?? null;

  useEffect(() => {
    if (editingListingId !== null && status && editingListing === null) {
      setEditingListingId(null);
    }
  }, [editingListing, editingListingId, status]);

  const muteListing = (listing: DealWatchListing) => {
    if (listing.notificationsMuted) {
      void controls.setListingMute(listing.id, 0);
      return;
    }
    const days = promptForMuteDays(listing.displayTitle, showToast);
    if (days !== null) void controls.setListingMute(listing.id, days);
  };

  return (
    <BottomSheet
      open={open}
      title="Deal Watch"
      description="Watch listings, whole queries and Haveno XMR offers. Configuration and history stay on vanpi."
      onClose={onClose}
    >
      <div className="deal-watch-sheet__toolbar">
        <span aria-live="polite">
          {controls.busy ? 'Working…' : status ? 'Controls ready' : 'Loading…'}
        </span>
        <div>
          <button
            type="button"
            disabled={
              controls.busy ||
              !status ||
              status.listings.length + status.searches.length + status.havenoWatches.length === 0
            }
            onClick={() => void controls.checkListings('all')}
          >
            Check all now
          </button>
          <button
            type="button"
            className="secondary-button"
            disabled={resource.refreshing || controls.busy || !open}
            onClick={() => void resource.refresh()}
          >
            {resource.refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      </div>

      {resource.error && <p className="error-message">{resource.error.message}</p>}
      {!status ? (
        <p className="deal-watch-sheet__empty">
          {resource.error ? 'Deal Watch data is unavailable.' : 'Loading Deal Watch…'}
        </p>
      ) : (
        <div className="deal-watch-sheet__sections" aria-busy={controls.busy}>
          <DealWatchScheduleForm
            schedule={status.schedule}
            disabled={controls.busy}
            onSave={controls.saveSchedule}
          />

          <section className="deal-watch-section" aria-labelledby="deal-watch-listings-title">
            <header className="deal-watch-section__header">
              <div>
                <h3 id="deal-watch-listings-title">Listing watches</h3>
                <p>Specific products compared with their configured alert threshold.</p>
              </div>
              <span>{status.listingSummary.count}</span>
            </header>
            <div className="deal-watch-card-list">
              {status.listings.length ? (
                status.listings.map((listing) => (
                  <ListingCard
                    listing={listing}
                    disabled={controls.busy}
                    onEdit={() => setEditingListingId(listing.id)}
                    onCheck={() => void controls.checkListings(listing.id)}
                    onMute={() => muteListing(listing)}
                    onRemove={() => {
                      if (window.confirm(`Remove ${listing.displayTitle}?`)) {
                        void controls.removeListing(listing.id);
                      }
                    }}
                    key={listing.id}
                  />
                ))
              ) : (
                <p className="deal-watch-section__empty">No listing watches configured.</p>
              )}
            </div>
            <DealWatchListingForm
              editing={editingListing}
              disabled={controls.busy}
              onCancel={() => setEditingListingId(null)}
              onSubmit={async (draft) => {
                const saved = editingListing
                  ? await controls.editListing(editingListing.id, draft)
                  : await controls.addListing(draft);
                if (saved) setEditingListingId(null);
                return saved;
              }}
            />
          </section>

          <DealWatchSearchSection searches={status.searches} controls={controls} />

          <DealWatchHavenoSection
            watches={status.havenoWatches}
            activity={status.havenoActivity}
            disabled={controls.busy}
            controls={controls}
          />
        </div>
      )}
    </BottomSheet>
  );
}
