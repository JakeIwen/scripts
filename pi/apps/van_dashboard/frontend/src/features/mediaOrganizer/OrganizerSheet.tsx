import { useState } from 'react';
import { BottomSheet } from '../../components/BottomSheet';
import { JobProgress } from './JobProgress';
import { ReviewTable } from './ReviewTable';
import { RunHistory } from './RunHistory';
import { SettingsForm } from './SettingsForm';
import { PlanState } from './schema';
import type { Organizer } from './useOrganizer';

export function OrganizerSheet({
  open,
  onClose,
  organizer: o,
}: {
  open: boolean;
  onClose: () => void;
  organizer: Organizer;
}) {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const status = o.resource.data;
  const dirty = Object.keys(o.draft).length > 0;
  const currentPlan = o.plan?.id === status?.latest_plan?.id ? o.plan : null;
  const editable =
    !!currentPlan &&
    (currentPlan.status === PlanState.Proposed || currentPlan.status === PlanState.Partial);
  const blocked = o.blocked || !status?.media_mounted;
  return (
    <BottomSheet open={open} onClose={onClose} title="Media organizer" className="organizer-sheet">
      <OrganizerToolbar
        organizer={o}
        blocked={blocked}
        dirty={dirty}
        editable={editable}
        autoAvailable={!!currentPlan?.rows.some((row) => row.auto_eligible)}
        settingsOpen={settingsOpen}
        toggleSettings={() => setSettingsOpen(!settingsOpen)}
      />
      {status && <JobProgress status={status} />}
      {dirty && (
        <p>
          Unsaved review edits. Apply selected saves these decisions before moving files.{' '}
          <button type="button" disabled={o.blocked} onClick={() => o.clearDraft()}>
            Discard edits
          </button>
        </p>
      )}
      {(o.error || o.resource.error || status?.last_error || status?.job?.error) && (
        <p className="error-message" role="alert">
          {o.error ?? o.resource.error?.message ?? status?.job?.error ?? status?.last_error}
        </p>
      )}
      {status && !status.media_mounted && (
        <p className="error-message">Media drive is not mounted.</p>
      )}
      {status && !status.qbittorrent_available && (
        <p>qBittorrent unavailable; tracked entries cannot be moved.</p>
      )}
      {status?.settings.mirror_rename && !status.mirror_available && (
        <p>
          Mirror unavailable. Check the organizer result before assuming mirror copies were renamed.
        </p>
      )}
      {settingsOpen && status && (
        <SettingsForm settings={status.settings} disabled={o.blocked} save={o.save} />
      )}
      {currentPlan ? (
        <section>
          <h3>Review plan</h3>
          {currentPlan.classifier_error && (
            <p className="error-message">{currentPlan.classifier_error}</p>
          )}
          <ReviewTable
            plan={currentPlan}
            draft={o.draft}
            onChange={o.change}
            disabled={blocked || !editable}
          />
        </section>
      ) : (
        <p>{status?.latest_plan ? 'Loading plan…' : 'Scan & propose to create a review plan.'}</p>
      )}
      <RunHistory
        runs={status?.recent_runs ?? []}
        disabled={blocked}
        onUndo={(id) => void o.undo(id)}
      />
    </BottomSheet>
  );
}

function OrganizerToolbar({
  organizer: o,
  blocked,
  dirty,
  editable,
  autoAvailable,
  settingsOpen,
  toggleSettings,
}: {
  organizer: Organizer;
  blocked: boolean;
  dirty: boolean;
  editable: boolean;
  autoAvailable: boolean;
  settingsOpen: boolean;
  toggleSettings: () => void;
}) {
  return (
    <div className="organizer-toolbar">
      <button
        type="button"
        className="primary-button"
        disabled={blocked || dirty}
        onClick={() => void o.scan()}
      >
        Scan &amp; propose
      </button>
      <button
        type="button"
        disabled={blocked || dirty || !editable || !autoAvailable}
        onClick={() => void o.applyAuto()}
      >
        Apply auto
      </button>
      <button
        type="button"
        disabled={blocked || !editable || !o.canApply}
        onClick={() => void o.applySelected()}
      >
        Apply selected
      </button>
      <button type="button" disabled={blocked} onClick={() => void o.alias()}>
        Rebuild links
      </button>
      <button type="button" aria-expanded={settingsOpen} onClick={toggleSettings}>
        Settings
      </button>
    </div>
  );
}
