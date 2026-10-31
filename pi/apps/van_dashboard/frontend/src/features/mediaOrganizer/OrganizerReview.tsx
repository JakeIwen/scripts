import { MediaHelp } from '../../components/media/MediaHelp';
import { ReviewTable } from './ReviewTable';
import { OrganizerToolbar } from './OrganizerToolbar';
import { organizerCopy as copy } from './content';
import { PlanState } from './schema';
import type { Organizer } from './useOrganizer';

export function OrganizerReview({ organizer: o }: { organizer: Organizer }) {
  const status = o.resource.data;
  const currentPlan = o.plan?.id === status?.latest_plan?.id ? o.plan : null;
  const editable =
    !!currentPlan &&
    (currentPlan.status === PlanState.Proposed || currentPlan.status === PlanState.Partial);
  const blocked = o.blocked || !status?.media_mounted;
  return (
    <>
      <OrganizerToolbar organizer={o} blocked={blocked} editable={editable} />
      {!!Object.keys(o.draft).length && (
        <p className="media-notice">
          {copy.dirty}{' '}
          <button
            type="button"
            className="secondary-button"
            disabled={o.blocked}
            onClick={o.clearDraft}
          >
            Discard edits
          </button>
        </p>
      )}
      {currentPlan ? (
        <section>
          <div className="media-section-heading">
            <h3>Review plan</h3>
            <MediaHelp label="Review plan">{copy.reviewHelp}</MediaHelp>
          </div>
          <p className="organizer-plan-summary">
            {o.selectedCount} selected · {currentPlan.settled_rows.length} processed · scanned{' '}
            {new Date(currentPlan.created_at).toLocaleString()}
          </p>
          {!editable && <p className="media-notice">{copy.stalePlan}</p>}
          {currentPlan.classifier_error && (
            <p className="error-message">
              Classifier could not complete: {currentPlan.classifier_error}. Review the proposals
              before applying.
            </p>
          )}
          <ReviewTable
            plan={currentPlan}
            draft={o.draft}
            onChange={o.change}
            disabled={blocked || !editable}
          />
        </section>
      ) : (
        !o.resource.error && <p>{status?.latest_plan ? 'Loading plan…' : copy.noPlan}</p>
      )}
    </>
  );
}
