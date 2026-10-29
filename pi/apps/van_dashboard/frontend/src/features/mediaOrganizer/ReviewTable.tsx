import { useId } from 'react';
import { StatusPill } from '../../components/StatusPill';
import {
  categories,
  Category,
  type Decision,
  type Decisions,
  type Plan,
  type PlanRow,
} from './schema';

export function rowDecision(row: PlanRow): Decision {
  return (
    row.decision ?? {
      category: row.proposal?.category ?? Category.Movies,
      show: row.proposal?.show ?? null,
      accepted: false,
    }
  );
}
interface Props {
  plan: Plan;
  draft: Decisions;
  disabled: boolean;
  onChange: (name: string, decision: Decision) => void;
}

export function ReviewTable({ plan, draft, disabled, onChange }: Props) {
  return (
    <div
      className="organizer-review organizer-review--responsive"
      role="table"
      aria-label="Organizer proposals"
    >
      <div className="organizer-review__head" role="row">
        {['Name / torrent', 'Category / show', 'Confidence / reason', 'Accept'].map((label) => (
          <span role="columnheader" key={label}>
            {label}
          </span>
        ))}
      </div>
      {plan.rows.map((row) => (
        <ReviewRow
          key={row.entry.name}
          row={row}
          shows={plan.existing_shows}
          decision={draft[row.entry.name] ?? rowDecision(row)}
          disabled={disabled}
          onChange={onChange}
        />
      ))}
      {!plan.rows.length && <p>No entries in this plan.</p>}
    </div>
  );
}

function ReviewRow({
  row,
  shows,
  decision,
  disabled,
  onChange,
}: {
  row: PlanRow;
  shows: Plan['existing_shows'];
  decision: Decision;
  disabled: boolean;
  onChange: Props['onChange'];
}) {
  const name = row.entry.name;
  const skipped = row.entry.skip_reason;
  const score = row.proposal?.confidence;
  const high = score !== undefined && score >= 0.85;
  const confidence =
    score === undefined
      ? 'No proposal'
      : `${high ? 'High' : score >= 0.6 ? 'Medium' : 'Low'} · ${Math.round(score * 100)}%`;
  const change = (patch: Partial<Decision>) => onChange(name, { ...decision, ...patch });
  return (
    <div
      role="row"
      className={`organizer-review__row${skipped ? ' organizer-review__row--skipped' : ''}`}
    >
      <div role="cell">
        <strong className="organizer-entry-name">{name}</strong>
        <small>
          {row.entry.tracked ? (row.entry.torrent_state ?? 'Unknown torrent state') : 'untracked'}
        </small>
        {row.auto_eligible && !skipped && <small>Auto eligible</small>}
      </div>
      {skipped ? (
        <div role="cell" className="organizer-review__skip">
          Skipped: {skipped}
        </div>
      ) : (
        <>
          <Destination
            name={name}
            decision={decision}
            shows={shows}
            disabled={disabled}
            change={change}
          />
          <div role="cell">
            <StatusPill dot={false} tone={high ? 'good' : 'warning'}>
              {confidence}
            </StatusPill>
            <small title={row.proposal?.reason}>
              {row.proposal?.reason ?? 'Choose a destination for review.'}
            </small>
          </div>
          <div role="cell">
            <label className="organizer-accept">
              <input
                type="checkbox"
                aria-label={`Accept ${name}`}
                checked={decision.accepted}
                disabled={disabled}
                onChange={(event) => change({ accepted: event.target.checked })}
              />
              <span>Accept</span>
            </label>
          </div>
        </>
      )}
    </div>
  );
}

function Destination({
  name,
  decision,
  shows,
  disabled,
  change,
}: {
  name: string;
  decision: Decision;
  shows: Plan['existing_shows'];
  disabled: boolean;
  change: (patch: Partial<Decision>) => void;
}) {
  const listId = useId();
  return (
    <div role="cell" className="organizer-destination">
      <select
        aria-label={`Category for ${name}`}
        value={decision.category}
        disabled={disabled}
        onChange={(event) =>
          change({
            category: event.target.value as Decision['category'],
            show: event.target.value === Category.Movies ? null : (decision.show ?? ''),
          })
        }
      >
        {categories.map((category) => (
          <option key={category}>{category}</option>
        ))}
      </select>
      {decision.category !== Category.Movies && (
        <>
          <input
            aria-label={`Show for ${name}`}
            list={listId}
            value={decision.show ?? ''}
            disabled={disabled}
            onChange={(event) => change({ show: event.target.value })}
          />
          <datalist id={listId}>
            {(shows[decision.category] ?? []).map((show) => (
              <option value={show} key={show} />
            ))}
          </datalist>
        </>
      )}
    </div>
  );
}
