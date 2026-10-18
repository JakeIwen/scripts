import { useEffect, useId, useState } from 'react';

import type { DealWatchHavenoDraft, DealWatchHavenoWatch } from './types';

export interface DealWatchHavenoFormProps {
  editing: DealWatchHavenoWatch | null;
  disabled: boolean;
  onSubmit: (draft: DealWatchHavenoDraft) => Promise<boolean>;
  onCancel: () => void;
}

type DraftUpdate = (field: keyof DealWatchHavenoDraft, value: string) => void;

const EMPTY_DRAFT: DealWatchHavenoDraft = {
  thresholdPct: '',
  feePct: '0',
  paymentMethod: 'ZELLE',
  currency: 'USD',
  minXmr: '',
  maxXmr: '',
  title: '',
};

function HavenoMarketFields({
  draft,
  disabled,
  update,
}: {
  draft: DealWatchHavenoDraft;
  disabled: boolean;
  update: DraftUpdate;
}) {
  const thresholdId = useId();
  const feeId = useId();
  const paymentId = useId();
  const currencyId = useId();
  return (
    <>
      <label htmlFor={thresholdId}>
        <span>Max % above market</span>
        <input
          id={thresholdId}
          type="number"
          min="-50"
          max="50"
          step="0.01"
          inputMode="decimal"
          placeholder="1.00"
          value={draft.thresholdPct}
          disabled={disabled}
          required
          onChange={(event) => update('thresholdPct', event.target.value)}
        />
      </label>
      <label htmlFor={feeId}>
        <span>Fee %</span>
        <input
          id={feeId}
          type="number"
          min="0"
          max="20"
          step="0.01"
          inputMode="decimal"
          value={draft.feePct}
          disabled={disabled}
          onChange={(event) => update('feePct', event.target.value)}
        />
      </label>
      <label htmlFor={paymentId}>
        <span>Payment method</span>
        <input
          id={paymentId}
          type="text"
          pattern="[A-Z][A-Z0-9_]{1,39}"
          maxLength={40}
          autoComplete="off"
          value={draft.paymentMethod}
          disabled={disabled}
          required
          onChange={(event) => update('paymentMethod', event.target.value.toUpperCase())}
        />
      </label>
      <label htmlFor={currencyId}>
        <span>Currency</span>
        <input
          id={currencyId}
          type="text"
          pattern="[A-Z]{3}"
          maxLength={3}
          autoComplete="off"
          value={draft.currency}
          disabled={disabled}
          required
          onChange={(event) => update('currency', event.target.value.toUpperCase())}
        />
      </label>
    </>
  );
}

function HavenoOptionalFields({
  draft,
  disabled,
  update,
}: {
  draft: DealWatchHavenoDraft;
  disabled: boolean;
  update: DraftUpdate;
}) {
  const minId = useId();
  const maxId = useId();
  const titleId = useId();
  return (
    <>
      <label htmlFor={minId}>
        <span>
          Minimum XMR <small>optional</small>
        </span>
        <input
          id={minId}
          type="number"
          min="0.000000000001"
          step="any"
          inputMode="decimal"
          value={draft.minXmr}
          disabled={disabled}
          onChange={(event) => update('minXmr', event.target.value)}
        />
      </label>
      <label htmlFor={maxId}>
        <span>
          Maximum XMR <small>optional</small>
        </span>
        <input
          id={maxId}
          type="number"
          min="0.000000000001"
          step="any"
          inputMode="decimal"
          value={draft.maxXmr}
          disabled={disabled}
          onChange={(event) => update('maxXmr', event.target.value)}
        />
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
          placeholder="Zelle XMR"
          value={draft.title}
          disabled={disabled}
          onChange={(event) => update('title', event.target.value)}
        />
      </label>
    </>
  );
}

export function DealWatchHavenoForm({
  editing,
  disabled,
  onSubmit,
  onCancel,
}: DealWatchHavenoFormProps) {
  const [draft, setDraft] = useState<DealWatchHavenoDraft>(EMPTY_DRAFT);

  useEffect(() => {
    setDraft(
      editing
        ? {
            thresholdPct: editing.thresholdPct,
            feePct: editing.feePct,
            paymentMethod: editing.paymentMethod,
            currency: editing.currency,
            minXmr: editing.minXmr ?? '',
            maxXmr: editing.maxXmr ?? '',
            title: editing.title ?? '',
          }
        : EMPTY_DRAFT,
    );
  }, [editing]);

  const update: DraftUpdate = (field, value) => {
    setDraft((current) => ({ ...current, [field]: value }));
  };

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const saved = await onSubmit({
      thresholdPct: draft.thresholdPct.trim(),
      feePct: draft.feePct.trim() || '0',
      paymentMethod: draft.paymentMethod.trim().toUpperCase(),
      currency: draft.currency.trim().toUpperCase(),
      minXmr: draft.minXmr.trim(),
      maxXmr: draft.maxXmr.trim(),
      title: draft.title.trim(),
    });
    if (saved && editing === null) setDraft(EMPTY_DRAFT);
  };

  return (
    <form className="deal-watch-form" onSubmit={(event) => void submit(event)}>
      <h4>{editing ? `Edit ${editing.displayTitle}` : 'Add an XMR offer watch'}</h4>
      <div className="deal-watch-form__grid">
        <HavenoMarketFields draft={draft} disabled={disabled} update={update} />
        <HavenoOptionalFields draft={draft} disabled={disabled} update={update} />
      </div>
      <div className="deal-watch-form__actions">
        {editing && (
          <button type="button" className="secondary-button" disabled={disabled} onClick={onCancel}>
            Cancel
          </button>
        )}
        <button type="submit" disabled={disabled}>
          {editing ? 'Save changes' : 'Add XMR watch'}
        </button>
      </div>
    </form>
  );
}
