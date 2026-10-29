import { Tile, ServiceLink } from '../../components/Tile';
import { usePaneLink } from '../../dashboard/paneLinks';
import { OrganizerSheet } from './OrganizerSheet';
import { useOrganizer } from './useOrganizer';
import './mediaOrganizer.css';

export function MediaOrganizerFeature() {
  const [open, setOpen] = usePaneLink('media-organizer');
  const organizer = useOrganizer(open);
  const status = organizer.resource.data;
  const plan = status?.latest_plan;
  const summary = status
    ? `${status.new_count} in New · ${plan?.row_count ?? 0} proposed · ${Math.max(0, (plan?.row_count ?? 0) - (plan?.auto_count ?? 0))} to review`
    : organizer.resource.error
      ? 'Organizer unavailable'
      : 'Loading organizer…';
  return (
    <>
      <Tile icon="" title="Movies & TV" summary={summary}>
        <div className="organizer-tile-actions">
          <ServiceLink port={8789} className="organizer-service-link">
            Open the library
          </ServiceLink>
          <button type="button" onClick={() => setOpen(true)}>
            Media organizer
          </button>
        </div>
        {organizer.busyStage && <small role="status">Running: {organizer.busyStage}</small>}
      </Tile>
      <OrganizerSheet open={open} onClose={() => setOpen(false)} organizer={organizer} />
    </>
  );
}
