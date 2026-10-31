import { RunDetails } from './RunDetails';
import type { RunSummary } from './schema';
import { MediaAction } from '../../components/media/MediaAction';
import { organizerCopy as copy, aliasLabels } from './content';
export function RunHistory({
  runs,
  disabled,
  workingRunId,
  onUndo,
}: {
  runs: RunSummary[];
  disabled: boolean;
  workingRunId?: string | null;
  onUndo: (id: string) => void;
}) {
  return (
    <section className="organizer-history">
      <h3>Run history</h3>
      {!runs.length && <p>No runs yet.</p>}
      {[...runs]
        .sort((a, b) => b.started_at.localeCompare(a.started_at))
        .map((run) => (
          <article className="organizer-run" key={run.id}>
            <div>
              <strong>{new Date(run.started_at).toLocaleString()}</strong>
              <p>
                {run.moved_count} moved · {run.failed_count} failed · {run.reverted_count} reverted
              </p>
              <small>
                Links: {run.alias_status ? aliasLabels[run.alias_status] : 'Not scheduled'}
                {run.undone_at ? ' · Undone' : ''}
              </small>
              {run.error && (
                <p className="error-message">
                  Run needs attention: {run.error}. View items for details before retrying.
                </p>
              )}
            </div>
            {run.undoable && !run.undone_at && (
              <MediaAction
                label="Undo"
                help={copy.undoHelp}
                className="danger-button"
                disabled={disabled}
                onClick={() => {
                  if (window.confirm(copy.undoConfirm)) onUndo(run.id);
                }}
              >
                {workingRunId === run.id ? 'Undoing…' : 'Undo'}
              </MediaAction>
            )}
            <RunDetails run={run} />
          </article>
        ))}
    </section>
  );
}
