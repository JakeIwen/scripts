import { useState } from 'react';

import { StatusPill } from '../../components/StatusPill';
import type { DealWatchControls } from './controls';
import { DealWatchSearchForm } from './DealWatchSearchForm';
import { ExternalDealLink } from './ExternalDealLink';
import { checkAge, searchState } from './presentation';
import type { DealWatchSavedSearch, DealWatchSearchDraft, DealWatchSearchResult } from './types';

function SearchActions({
  search,
  disabled,
  onCheck,
  onRemove,
  onEdit,
}: Omit<SearchCardProps, 'onDismiss'>) {
  const [editing, setEditing] = useState(false);
  if (editing) {
    return (
      <DealWatchSearchForm
        editing={search}
        disabled={disabled}
        onCancel={() => setEditing(false)}
        onSubmit={async (draft) => {
          const saved = await onEdit(draft);
          if (saved) setEditing(false);
          return saved;
        }}
      />
    );
  }
  return (
    <div className="deal-watch-card__actions">
      <button type="button" disabled={disabled} onClick={onCheck}>
        Check
      </button>
      <button type="button" disabled={disabled} onClick={() => setEditing(true)}>
        Edit
      </button>
      <button type="button" className="danger-button" disabled={disabled} onClick={onRemove}>
        Remove
      </button>
    </div>
  );
}

interface SearchResultRowProps {
  result: DealWatchSearchResult;
  disabled: boolean;
  onDismiss: () => void;
}

function SearchResultRow({ result, disabled, onDismiss }: SearchResultRowProps) {
  const details = [result.price, result.shipping].filter(
    (detail): detail is string => detail !== null,
  );
  return (
    <li className="deal-watch-result">
      <div>
        <ExternalDealLink url={result.url}>{result.title}</ExternalDealLink>
        <small>{details.length ? details.join(' · ') : 'No price details returned'}</small>
      </div>
      <span>
        <time dateTime={new Date(result.firstSeenAt * 1_000).toISOString()}>
          First seen {checkAge(result.firstSeenAt)}
        </time>
        <button type="button" className="danger-button" disabled={disabled} onClick={onDismiss}>
          Dismiss
        </button>
      </span>
    </li>
  );
}

interface SearchCardProps {
  search: DealWatchSavedSearch;
  disabled: boolean;
  onCheck: () => void;
  onRemove: () => void;
  onDismiss: (result: DealWatchSearchResult) => void;
  onEdit: (draft: DealWatchSearchDraft) => Promise<boolean>;
}

function SearchCard({ search, disabled, onCheck, onRemove, onDismiss, onEdit }: SearchCardProps) {
  const state = searchState(search);
  return (
    <article className={`deal-watch-card deal-watch-card--${state.tone}`}>
      <header className="deal-watch-card__header">
        <div>
          <ExternalDealLink url={search.url}>{search.displayTitle}</ExternalDealLink>
          <small>{search.parser} saved search</small>
        </div>
        <StatusPill tone={state.tone}>{state.label}</StatusPill>
      </header>
      <dl className="deal-watch-card__facts">
        <div>
          <dt>Last check</dt>
          <dd>{checkAge(search.lastCheckedAt)}</dd>
        </div>
        <div>
          <dt>Visible now</dt>
          <dd>{search.resultCount}</dd>
        </div>
        <div>
          <dt>Dismissed</dt>
          <dd>{search.dismissedCount}</dd>
        </div>
        <div>
          <dt>Known listings</dt>
          <dd>{search.knownCount}</dd>
        </div>
      </dl>
      {search.lastError && <p className="deal-watch-card__error">{search.lastError}</p>}
      <SearchActions
        search={search}
        disabled={disabled}
        onCheck={onCheck}
        onRemove={onRemove}
        onEdit={onEdit}
      />
      {search.results.length ? (
        <ul className="deal-watch-results">
          {search.results.map((result) => (
            <SearchResultRow
              result={result}
              disabled={disabled}
              onDismiss={() => onDismiss(result)}
              key={result.itemId}
            />
          ))}
        </ul>
      ) : (
        <p className="deal-watch-card__empty">No visible results.</p>
      )}
    </article>
  );
}

export function DealWatchSearchSection({
  searches,
  controls,
}: {
  searches: DealWatchSavedSearch[];
  controls: DealWatchControls;
}) {
  return (
    <section className="deal-watch-section" aria-labelledby="deal-watch-searches-title">
      <header className="deal-watch-section__header">
        <div>
          <h3 id="deal-watch-searches-title">Saved searches</h3>
          <p>Current, non-dismissed results from each watched query.</p>
        </div>
        <span>{searches.length}</span>
      </header>
      <div className="deal-watch-card-list">
        {searches.length ? (
          searches.map((search) => (
            <SearchCard
              key={search.id}
              search={search}
              disabled={controls.busy}
              onCheck={() => void controls.checkSearch(search.id)}
              onEdit={(draft) => controls.editSearch(search.id, draft)}
              onRemove={() => {
                if (window.confirm(`Remove ${search.displayTitle}?`))
                  void controls.removeSearch(search.id);
              }}
              onDismiss={(result) => {
                if (window.confirm(`Permanently hide ${result.title}?`)) {
                  void controls.dismissSearchResult(search.id, result.itemId);
                }
              }}
            />
          ))
        ) : (
          <p className="deal-watch-section__empty">No saved searches configured.</p>
        )}
      </div>
      <DealWatchSearchForm disabled={controls.busy} onSubmit={controls.addSearch} />
    </section>
  );
}
