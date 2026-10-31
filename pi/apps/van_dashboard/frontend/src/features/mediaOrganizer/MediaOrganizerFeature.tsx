import { Tile, ServiceLink } from '../../components/Tile';
import { MovieIcon } from '../../components/MovieIcon';
import { StatusPill } from '../../components/StatusPill';
import { TooltipButton } from '../../components/TooltipButton';
import { usePaneLink } from '../../dashboard/paneLinks';
import { OrganizerSheet } from './OrganizerSheet';
import { useOrganizer } from './useOrganizer';
import { IptWatchSheet } from '../iptWatch/IptWatchSheet';
import { useIptWatch } from '../iptWatch/useIptWatch';
import { releaseCount } from '../iptWatch/content';
import { tileCopy as copy, stageLabels } from './content';
import './mediaOrganizer.css';

export function MediaOrganizerFeature() {
  const [open, setOpen] = usePaneLink('media-organizer');
  const [watchOpen, setWatchOpen] = usePaneLink('ipt-watch');
  const watch = useIptWatch(watchOpen);
  const organizer = useOrganizer(open);
  const status = organizer.resource.data;
  return (
    <>
      <Tile
        icon={<MovieIcon />}
        title={copy.title}
        className="organizer-tile"
        onClick={() => setOpen(true)}
        ariaLabel="Open media organizer"
        summary={
          <div className="organizer-tile-summary">
            {status ? (
              <StatusPill detail={copy.newHelp}>{status.new_count} in New</StatusPill>
            ) : (
              <span>
                {organizer.resource.error ? 'Organizer unavailable' : 'Loading organizer…'}
              </span>
            )}
            {watch.resource.data ? (
              <StatusPill
                tone={watch.resource.data.new_candidate_count ? 'good' : 'neutral'}
                detail={copy.watchHelp}
              >
                {releaseCount(watch.resource.data.new_candidate_count)}
              </StatusPill>
            ) : (
              <span>{watch.resource.error ? 'Watchlist unavailable' : 'Loading watchlist…'}</span>
            )}
          </div>
        }
      >
        <div className="organizer-tile-actions">
          <ServiceLink port={8789} className="secondary-button">
            {copy.library}
          </ServiceLink>
          <TooltipButton
            className="secondary-button"
            tooltip={copy.organizerHelp}
            onClick={() => setOpen(true)}
          >
            {copy.organizer}
          </TooltipButton>
          <TooltipButton
            className="secondary-button"
            tooltip={copy.watchHelp}
            onClick={() => setWatchOpen(true)}
          >
            {copy.watch}
          </TooltipButton>
          <ServiceLink port={8787} className="secondary-button">
            {copy.books}
          </ServiceLink>
        </div>
        {organizer.busyStage && <small role="status">{stageLabels[organizer.busyStage]}…</small>}
      </Tile>
      <IptWatchSheet open={watchOpen} onClose={() => setWatchOpen(false)} watch={watch} />
      <OrganizerSheet open={open} onClose={() => setOpen(false)} organizer={organizer} />
    </>
  );
}
