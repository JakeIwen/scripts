import { useCallback, useState } from 'react';
import { usePollingResource } from '../../hooks/usePollingResource';
import { StatusPill } from '../../components/StatusPill';
import { fetchRun } from './api';
import { ItemState, MirrorState, type RunSummary } from './schema';
import { organizerCopy as copy, itemLabels } from './content';

export function RunDetails({ run }: { run: RunSummary }) {
  const [open, setOpen] = useState(false);
  const load = useCallback((signal: AbortSignal) => fetchRun(run.id, signal), [run.id]);
  const resource = usePollingResource({ load, enabled: open, intervalMs: 5000 });
  return (
    <div className="organizer-run-details">
      <button
        type="button"
        className="secondary-button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        {open ? copy.hideItems : copy.viewItems}
      </button>
      {open && (
        <>
          {resource.initialLoading && <p role="status">{copy.loadingItems}</p>}
          {resource.error && (
            <p className="error-message" role="alert">
              {copy.itemsError}{' '}
              <button
                type="button"
                className="secondary-button"
                disabled={resource.refreshing}
                onClick={() => void resource.refresh()}
              >
                {copy.refresh}
              </button>
            </p>
          )}
          {resource.data?.items.map((item) => (
            <article className="organizer-run-item" key={item.name}>
              <strong>{item.name}</strong>{' '}
              <StatusPill tone={item.status === ItemState.Failed ? 'warning' : 'neutral'}>
                {itemLabels[item.status]}
              </StatusPill>
              <p>
                {copy.from} <code>{item.old_path}</code>
              </p>
              <p>
                {copy.to} <code>{item.new_path}</code>
              </p>
              {item.error && (
                <p className="error-message">
                  {copy.itemError} {item.error}
                </p>
              )}
              {item.mirror_state === MirrorState.Pending && (
                <StatusPill tone="warning" detail={copy.mirrorHelp}>
                  {copy.mirrorPending}
                </StatusPill>
              )}
            </article>
          ))}
          {resource.data && !resource.data.items.length && <p>{copy.noItems}</p>}
        </>
      )}
    </div>
  );
}
