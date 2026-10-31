import { organizerCopy as copy } from './content';
import type { Organizer } from './useOrganizer';

export function OrganizerError({ organizer: o }: { organizer: Organizer }) {
  const status = o.resource.data;
  return (
    <>
      {(o.error || o.resource.error) && (
        <div className="error-message" role="alert">
          <p>{o.error ?? copy.loadError}</p>
          <button
            className="secondary-button"
            type="button"
            disabled={o.resource.refreshing}
            onClick={() => {
              o.clearError();
              void o.refresh();
            }}
          >
            {copy.refresh}
          </button>
        </div>
      )}
      {!status && !o.resource.error && <p role="status">Loading organizer…</p>}
    </>
  );
}
