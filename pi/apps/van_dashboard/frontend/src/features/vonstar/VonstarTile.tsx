import { StatusPill } from '../../components/StatusPill';
import { Tile, TileStatusAnnouncement } from '../../components/Tile';
import { actionConfirmation, attemptLabel, describeVonstar, resultAttempt } from './presentation';
import { VonstarActions } from './VonstarActions';
import type { VonstarActionName, VonstarController } from './types';
import './vonstar.css';

export function VonstarTile({
  controller,
  onOpen,
}: {
  controller: VonstarController;
  onOpen: () => void;
}) {
  const presentation = describeVonstar(controller.status, controller.busy);
  const controlsEnabled =
    controller.status?.available === true &&
    controller.status.mode === 'execute' &&
    !controller.status.busy &&
    !controller.busy;
  const last = controller.lastAttempt ?? resultAttempt(controller.status?.lastResult ?? null);

  const run = (action: VonstarActionName) => {
    if (!window.confirm(actionConfirmation(action))) return;
    void controller.perform(action);
  };

  return (
    <Tile
      icon="⭐"
      title="vOnStar"
      summary={
        controller.error && !controller.status ? controller.error.message : presentation.summary
      }
      status={<StatusPill tone={presentation.tone}>{presentation.label}</StatusPill>}
      tone={presentation.tone}
      className="vonstar-tile"
    >
      <button
        className="vonstar-open"
        type="button"
        aria-label="Open vOnStar status"
        onClick={onOpen}
      />
      <VonstarActions disabled={!controlsEnabled} onAction={run} />
      <div className="vonstar-last-action" role="status" aria-live="polite">
        <span>Last action</span>
        <span>{attemptLabel(last)}</span>
      </div>
      <TileStatusAnnouncement>
        {controller.error && controller.status
          ? `Status refresh failed · ${controller.error.message}`
          : ''}
      </TileStatusAnnouncement>
    </Tile>
  );
}
