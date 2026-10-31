import { useId } from 'react';
import { StatusPill } from '../../components/StatusPill';
import { MediaHelp } from '../../components/media/MediaHelp';
import { organizerCopy as copy, skipLabels } from './content';
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

/** Rows you can act on first; skipped rows (hidden files, unfinished downloads) last. */
function reviewOrder(rows: PlanRow[]) {
  return [...rows].sort((a, b) => Number(!!a.entry.skip_reason) - Number(!!b.entry.skip_reason));
}

export function ReviewTable({ plan, draft, disabled, onChange }: Props) {
  return (
    <div
      className="organizer-review organizer-review--responsive"
      role="table"
      aria-label="Organizer proposals"
    >
      <div className="organizer-review__head" role="row">
        <span role="columnheader">File / torrent</span>
        <span role="columnheader">
          Destination <MediaHelp label="Destination">{copy.destinationHelp}</MediaHelp>
        </span>
        <span role="columnheader">
          Proposal <MediaHelp label="Confidence">{copy.confidenceHelp}</MediaHelp>
        </span>
        <span role="columnheader">
          Select <MediaHelp label="Selection">{copy.selectHelp}</MediaHelp>
        </span>
      </div>
      {reviewOrder(plan.rows).map((row) => (
        <ReviewRow
          key={row.entry.name}
          row={row}
          shows={plan.existing_shows}
          decision={draft[row.entry.name] ?? rowDecision(row)}
          settled={plan.settled_rows.includes(row.entry.name)}
          moved={plan.moved_rows.includes(row.entry.name)}
          edited={!!draft[row.entry.name]}
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
  settled,
  moved,
  edited,
  disabled: listDisabled,
  onChange,
}: {
  row: PlanRow;
  shows: Plan['existing_shows'];
  decision: Decision;
  settled: boolean;
  moved: boolean;
  edited?: boolean;
  disabled: boolean;
  onChange: Props['onChange'];
}) {
  const disabled = listDisabled || settled;
  const name = row.entry.name;
  const skipped = row.entry.skip_reason;
  const score = row.proposal?.confidence;
  const confidence = score === undefined ? 'No proposal' : `${Math.round(score * 100)}% confidence`;
  const change = (patch: Partial<Decision>) => onChange(name, { ...decision, ...patch });
  return (
    <div
      role="row"
      className={`organizer-review__row${skipped ? ' organizer-review__row--skipped' : ''}`}
    >
      <div role="cell">
        <strong className="organizer-entry-name">{name}</strong>
        <small>{row.entry.tracked ? 'Tracked by qBittorrent' : 'Not in qBittorrent'}</small>
        {row.auto_eligible && !skipped && !settled && !edited && (
          <StatusPill tone="good" detail={copy.autoEligibleHelp}>
            {copy.autoEligible}
          </StatusPill>
        )}
        {edited && <StatusPill detail={copy.editedHelp}>Edited</StatusPill>}
      </div>
      {skipped ? (
        <div role="cell" className="organizer-review__skip">
          Skipped: {skipLabels[skipped]}
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
            <StatusPill dot={false} detail={copy.confidenceHelp}>
              {confidence}
            </StatusPill>
            <small>{row.proposal?.reason ?? 'Choose a destination for review.'}</small>
          </div>
          <Selection
            name={name}
            settled={settled}
            moved={moved}
            decision={decision}
            disabled={disabled}
            change={change}
          />
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
      <label className="organizer-field-label">
        Category
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
      </label>
      {decision.category !== Category.Movies && (
        <>
          <label className="organizer-field-label">
            Show folder
            <input
              aria-label={`Show for ${name}`}
              list={listId}
              placeholder="Choose or enter a show"
              aria-invalid={!decision.show?.trim()}
              value={decision.show ?? ''}
              disabled={disabled}
              onChange={(event) => change({ show: event.target.value })}
            />
          </label>
          {!decision.show?.trim() && <small className="error-message">{copy.showRequired}</small>}
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

function Selection({
  name,
  settled,
  moved,
  decision,
  disabled,
  change,
}: {
  name: string;
  settled: boolean;
  moved: boolean;
  decision: Decision;
  disabled: boolean;
  change: (patch: Partial<Decision>) => void;
}) {
  return (
    <div role="cell">
      {settled ? (
        <StatusPill
          dot={false}
          tone={moved ? 'good' : 'warning'}
          detail={moved ? copy.movedHelp : copy.unconfirmedHelp}
        >
          {moved ? copy.moved : copy.unconfirmed}
        </StatusPill>
      ) : (
        <label className="organizer-accept">
          <input
            type="checkbox"
            aria-label={`Accept ${name}`}
            checked={decision.accepted}
            disabled={disabled}
            onChange={(event) => change({ accepted: event.target.checked })}
          />
          <span>Select</span>
        </label>
      )}
    </div>
  );
}
