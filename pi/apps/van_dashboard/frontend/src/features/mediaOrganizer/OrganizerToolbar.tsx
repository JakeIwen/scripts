import { MediaAction } from '../../components/media/MediaAction';
import { organizerCopy as copy } from './content';
import { reviewableRows, type Organizer } from './useOrganizer';

export function OrganizerToolbar({
  organizer: o,
  blocked,
  editable,
}: {
  organizer: Organizer;
  blocked: boolean;
  editable: boolean;
}) {
  const dirty = Object.keys(o.draft).length > 0;
  const autoAvailable = !!o.plan && reviewableRows(o.plan).some((row) => row.auto_eligible);
  const label = (text: string) => (o.actionLabel === text ? `${text}…` : text);
  return (
    <div className="organizer-toolbar">
      <MediaAction
        label={copy.scan}
        help={copy.scanHelp}
        disabled={blocked || dirty}
        onClick={() => void o.scan()}
      >
        {label(copy.scan)}
      </MediaAction>
      <MediaAction
        label={copy.auto}
        help={copy.autoHelp}
        disabled={blocked || dirty || !editable || !autoAvailable}
        onClick={() => void o.applyAuto()}
      >
        {label(copy.auto)}
      </MediaAction>
      <MediaAction
        label={copy.selected}
        help={copy.selectedHelp}
        disabled={blocked || !editable || !o.canApply}
        onClick={() => void o.applySelected()}
      >
        {label(copy.selected)}
      </MediaAction>
    </div>
  );
}
