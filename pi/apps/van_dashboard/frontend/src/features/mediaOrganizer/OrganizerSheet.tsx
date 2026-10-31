import { useState } from 'react';
import { BottomSheet } from '../../components/BottomSheet';
import { MediaAction } from '../../components/media/MediaAction';
import { JobProgress } from './JobProgress';
import { RunHistory } from './RunHistory';
import { SettingsForm } from './SettingsForm';
import { OrganizerReview } from './OrganizerReview';
import { OrganizerNotices } from './OrganizerNotices';
import { OrganizerError } from './OrganizerError';
import { organizerCopy as copy } from './content';
import { Stage } from './schema';
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
  const [view, setView] = useState('Review');
  const status = o.resource.data;
  const blocked = o.blocked || !status?.media_mounted;
  return (
    <BottomSheet open={open} onClose={onClose} title="Media organizer" className="organizer-sheet">
      <nav className="media-navigation" aria-label="Organizer views">
        {['Review', 'History', 'Settings'].map((label) => (
          <button
            key={label}
            type="button"
            className="secondary-button"
            aria-pressed={view === label}
            onClick={() => {
              setView(label);
              o.clearError();
            }}
          >
            {label}
          </button>
        ))}
      </nav>
      {status && (
        <JobProgress
          status={status}
          pendingStage={o.busyStage}
          showOutcome={
            view === 'Review' || (view === 'History' && status.job?.stage !== Stage.Propose)
          }
        />
      )}
      <OrganizerError organizer={o} />
      {status && (
        <div hidden={view !== 'Settings'}>
          <SettingsForm
            settings={status.settings}
            disabled={o.blocked}
            saving={o.actionLabel === 'Saving setting'}
            save={o.save}
          />
        </div>
      )}
      {status && view !== 'Settings' && <OrganizerNotices organizer={o} />}
      {view === 'Review' && <OrganizerReview organizer={o} />}
      {view === 'History' && open && (
        <>
          <MediaAction
            label={copy.links}
            help={copy.linksHelp}
            disabled={blocked}
            onClick={() => void o.alias()}
          >
            {o.busyStage === Stage.Alias || o.actionLabel === copy.links
              ? 'Rebuilding links…'
              : copy.links}
          </MediaAction>
          <RunHistory
            runs={status?.recent_runs ?? []}
            disabled={blocked}
            workingRunId={o.undoRunId}
            onUndo={(id) => void o.undo(id)}
          />
        </>
      )}
    </BottomSheet>
  );
}
