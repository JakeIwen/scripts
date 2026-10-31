import { useId, useState } from 'react';

import type { DealWatchSavedSearch, DealWatchSearchDraft } from './types';

export interface DealWatchSearchFormProps {
  disabled: boolean;
  editing?: DealWatchSavedSearch;
  onSubmit: (draft: DealWatchSearchDraft) => Promise<boolean>;
  onCancel?: () => void;
}

export function DealWatchSearchForm({
  disabled,
  editing,
  onSubmit,
  onCancel,
}: DealWatchSearchFormProps) {
  const headingId = useId();
  const parserId = useId();
  const urlId = useId();
  const titleId = useId();
  // Each editor mounts for one search; polling must not replace an unsaved draft.
  const [url, setUrl] = useState(editing?.url ?? '');
  const [title, setTitle] = useState(editing?.title ?? '');

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const saved = await onSubmit({ parser: 'ebay', url: url.trim(), title: title.trim() });
    if (saved && !editing) {
      setUrl('');
      setTitle('');
    }
  };

  return (
    <form
      className="deal-watch-form"
      aria-labelledby={headingId}
      onSubmit={(event) => void submit(event)}
    >
      <h4 id={headingId}>{editing ? `Edit ${editing.displayTitle}` : 'Add a query'}</h4>
      <div className="deal-watch-form__grid">
        <label htmlFor={parserId}>
          <span>Parser</span>
          <select id={parserId} defaultValue="ebay" disabled={disabled}>
            <option value="ebay">eBay</option>
          </select>
        </label>
        <label htmlFor={titleId}>
          <span>
            Title <small>optional</small>
          </span>
          <input
            id={titleId}
            type="text"
            maxLength={160}
            autoComplete="off"
            placeholder="Query name"
            autoFocus={Boolean(editing)}
            value={title}
            disabled={disabled}
            onChange={(event) => setTitle(event.target.value)}
          />
        </label>
        <label className="deal-watch-form__wide" htmlFor={urlId}>
          <span>eBay search URL</span>
          <input
            id={urlId}
            type="url"
            inputMode="url"
            autoComplete="url"
            placeholder="https://www.ebay.com/sch/i.html?…"
            value={url}
            disabled={disabled}
            required
            onChange={(event) => setUrl(event.target.value)}
          />
        </label>
      </div>
      <div className="deal-watch-form__actions">
        {editing && (
          <button type="button" disabled={disabled} onClick={onCancel}>
            Cancel
          </button>
        )}
        <button type="submit" disabled={disabled}>
          {editing ? 'Save changes' : 'Add query watch'}
        </button>
      </div>
    </form>
  );
}
