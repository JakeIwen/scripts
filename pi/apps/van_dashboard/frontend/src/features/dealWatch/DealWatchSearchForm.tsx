import { useId, useState } from 'react';

import type { DealWatchSearchDraft } from './types';

export interface DealWatchSearchFormProps {
  disabled: boolean;
  onSubmit: (draft: DealWatchSearchDraft) => Promise<boolean>;
}

export function DealWatchSearchForm({ disabled, onSubmit }: DealWatchSearchFormProps) {
  const parserId = useId();
  const urlId = useId();
  const titleId = useId();
  const [url, setUrl] = useState('');
  const [title, setTitle] = useState('');

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const saved = await onSubmit({ parser: 'ebay', url: url.trim(), title: title.trim() });
    if (saved) {
      setUrl('');
      setTitle('');
    }
  };

  return (
    <form className="deal-watch-form" onSubmit={(event) => void submit(event)}>
      <h4>Add a query</h4>
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
        <button type="submit" disabled={disabled}>
          Add query watch
        </button>
      </div>
    </form>
  );
}
