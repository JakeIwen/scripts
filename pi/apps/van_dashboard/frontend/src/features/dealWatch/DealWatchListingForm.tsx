import { useEffect, useId, useState } from 'react';

import type { DealWatchListing, DealWatchListingDraft } from './types';

export interface DealWatchListingFormProps {
  editing: DealWatchListing | null;
  disabled: boolean;
  onSubmit: (draft: DealWatchListingDraft) => Promise<boolean>;
  onCancel: () => void;
}

export function DealWatchListingForm({
  editing,
  disabled,
  onSubmit,
  onCancel,
}: DealWatchListingFormProps) {
  const parserId = useId();
  const thresholdId = useId();
  const urlId = useId();
  const titleId = useId();
  const [threshold, setThreshold] = useState('');
  const [url, setUrl] = useState('');
  const [title, setTitle] = useState('');

  useEffect(() => {
    setThreshold(editing?.threshold ?? '');
    setUrl(editing?.url ?? '');
    setTitle(editing?.title ?? '');
  }, [editing]);

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const saved = await onSubmit({
      parser: 'amazon',
      threshold,
      url: url.trim(),
      title: title.trim(),
    });
    if (saved && editing === null) {
      setThreshold('');
      setUrl('');
      setTitle('');
    }
  };

  return (
    <form className="deal-watch-form" onSubmit={(event) => void submit(event)}>
      <h4>{editing ? `Edit ${editing.displayTitle}` : 'Add a listing'}</h4>
      <div className="deal-watch-form__grid">
        <label htmlFor={parserId}>
          <span>Parser</span>
          <select id={parserId} defaultValue="amazon" disabled={disabled}>
            <option value="amazon">Amazon</option>
          </select>
        </label>
        <label htmlFor={thresholdId}>
          <span>Alert below</span>
          <input
            id={thresholdId}
            type="number"
            min="0.01"
            step="0.01"
            inputMode="decimal"
            placeholder="55.00"
            value={threshold}
            disabled={disabled}
            required
            onChange={(event) => setThreshold(event.target.value)}
          />
        </label>
        <label className="deal-watch-form__wide" htmlFor={urlId}>
          <span>Product URL</span>
          <input
            id={urlId}
            type="url"
            inputMode="url"
            autoComplete="url"
            placeholder="https://…"
            value={url}
            disabled={disabled}
            required
            onChange={(event) => setUrl(event.target.value)}
          />
        </label>
        <label className="deal-watch-form__wide" htmlFor={titleId}>
          <span>
            Title <small>optional</small>
          </span>
          <input
            id={titleId}
            type="text"
            maxLength={160}
            autoComplete="off"
            placeholder="Friendly item name"
            value={title}
            disabled={disabled}
            onChange={(event) => setTitle(event.target.value)}
          />
        </label>
      </div>
      <div className="deal-watch-form__actions">
        {editing && (
          <button type="button" className="secondary-button" disabled={disabled} onClick={onCancel}>
            Cancel
          </button>
        )}
        <button type="submit" disabled={disabled}>
          {editing ? 'Save changes' : 'Add listing watch'}
        </button>
      </div>
    </form>
  );
}
