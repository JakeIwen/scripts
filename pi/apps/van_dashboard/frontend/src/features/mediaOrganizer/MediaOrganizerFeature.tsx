import { Tile, ServiceLink } from '../../components/Tile';
import { MovieIcon } from '../../components/MovieIcon';
import { usePaneLink } from '../../dashboard/paneLinks';
import { OrganizerSheet } from './OrganizerSheet';
import { useOrganizer } from './useOrganizer';
import { IptWatchSheet } from '../iptWatch/IptWatchSheet';
import { useIptWatch } from '../iptWatch/useIptWatch';
import './mediaOrganizer.css';

export function MediaOrganizerFeature() {
  const [open, setOpen] = usePaneLink('media-organizer');
  const [watchOpen, setWatchOpen] = usePaneLink('ipt-watch');
  const watch = useIptWatch(watchOpen);
  const newCandidates = watch.resource.data?.new_candidate_count ?? 0;
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
      <Tile
        icon={<MovieIcon />}
        title="Movies, TV, Audiobooks"
        summary={`${summary}${newCandidates > 0 ? ` · ${newCandidates} new episodes on IPT` : ''}`}
      >
        <div className="organizer-tile-actions">
          <ServiceLink port={8789} className="organizer-service-link">
            Open the library
          </ServiceLink>
          <button type="button" onClick={() => setOpen(true)}>
            Media organizer
          </button>
          <button type="button" onClick={() => setWatchOpen(true)}>
            Watchlist
          </button>
          <ServiceLink port={8787} className="organizer-service-link">
            Audiobooks
          </ServiceLink>
        </div>
        {organizer.busyStage && <small role="status">Running: {organizer.busyStage}</small>}
      </Tile>
      <IptWatchSheet open={watchOpen} onClose={() => setWatchOpen(false)} watch={watch} />
      <OrganizerSheet open={open} onClose={() => setOpen(false)} organizer={organizer} />
    </>
  );
}
