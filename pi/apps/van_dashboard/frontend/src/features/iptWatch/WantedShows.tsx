import { formatRelativeTime } from '../../utils/format';
import { MediaHelp } from '../../components/media/MediaHelp';
import { MediaAction } from '../../components/media/MediaAction';
import { StatusPill } from '../../components/StatusPill';
import { Source, WantAction, type WantedShow, type WantRequest, type WatchPending } from './schema';
import { watchCopy as copy, sourceLabels, pendingLabels } from './content';
import { AddShow } from './AddShow';

type Props = {
  shows: WantedShow[];
  disabled: boolean;
  pending?: WatchPending | null;
  change: (payload: WantRequest) => Promise<boolean>;
};
export function WantedShows({ shows, disabled, pending, change }: Props) {
  return (
    <section className="ipt-watch-section">
      <div className="media-section-heading">
        <h3>Wanted shows</h3>
        <MediaHelp label="Wanted shows">{copy.wantedHelp}</MediaHelp>
      </div>
      {!shows.length && <p>No wanted shows yet.</p>}
      {shows.map((show) => (
        <WantedShowRow
          key={show.name}
          show={show}
          disabled={disabled}
          pending={pending}
          change={change}
        />
      ))}
      <AddShow disabled={disabled} pending={pending?.action === WantAction.Add} add={change} />
    </section>
  );
}
function WantedShowRow({
  show,
  disabled,
  pending,
  change,
}: Omit<Props, 'shows'> & { show: WantedShow }) {
  const pauseAction = show.paused ? WantAction.Resume : WantAction.Pause;
  const pauseLabel = show.paused ? 'Resume' : 'Pause';
  return (
    <article className="ipt-watch-row">
      <div className="ipt-watch-details">
        <strong>{show.name}</strong>{' '}
        <StatusPill detail={show.source === Source.Library ? copy.libraryHelp : copy.manualHelp}>
          {sourceLabels[show.source]}
        </StatusPill>
        {show.paused && (
          <StatusPill tone="warning" detail={copy.pauseHelp}>
            Paused
          </StatusPill>
        )}
        <p>
          {show.have_season === null
            ? 'On disk unknown'
            : `On disk: S${String(show.have_season).padStart(2, '0')}${show.have_episode === null ? ' (last episode unknown)' : `E${String(show.have_episode).padStart(2, '0')}`}`}
          {show.last_watched !== null && ` · Last watched ${formatRelativeTime(show.last_watched)}`}
        </p>
      </div>
      <div className="ipt-watch-actions">
        <MediaAction
          label={`${pauseLabel} ${show.name}`}
          help={show.paused ? copy.resumeHelp : copy.pauseHelp}
          disabled={disabled}
          aria-label={`${pauseLabel} ${show.name}`}
          onClick={() => void change({ action: pauseAction, name: show.name })}
        >
          {pending?.id === show.name && pending.action === pauseAction
            ? pendingLabels[pauseAction]
            : pauseLabel}
        </MediaAction>
        {show.source === Source.Manual && (
          <MediaAction
            label={`Remove ${show.name}`}
            help={copy.removeHelp}
            disabled={disabled}
            aria-label={`Remove ${show.name}`}
            onClick={() => void change({ action: WantAction.Remove, name: show.name })}
          >
            {pending?.id === show.name && pending.action === WantAction.Remove
              ? pendingLabels[WantAction.Remove]
              : 'Remove'}
          </MediaAction>
        )}
      </div>
    </article>
  );
}
