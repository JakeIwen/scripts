import type { RunSummary } from './schema';
export function RunHistory({
  runs,
  disabled,
  onUndo,
}: {
  runs: RunSummary[];
  disabled: boolean;
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
                Links: {run.alias_status ?? 'Not scheduled'}
                {run.undone_at ? ' · Undone' : ''}
              </small>
              {run.error && <p className="error-message">{run.error}</p>}
            </div>
            {run.undoable && !run.undone_at && (
              <button
                type="button"
                className="danger-button"
                disabled={disabled}
                onClick={() => {
                  if (
                    window.confirm(
                      'Undo this organizer run? Moved files and mirror copies will return to their previous locations.',
                    )
                  )
                    onUndo(run.id);
                }}
              >
                Undo
              </button>
            )}
          </article>
        ))}
    </section>
  );
}
