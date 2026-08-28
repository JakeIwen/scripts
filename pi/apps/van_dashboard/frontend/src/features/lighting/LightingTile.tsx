import { StatusPill } from '../../components/StatusPill';
import { Tile } from '../../components/Tile';
import { LightingSlider } from './LightingSlider';
import {
  averageGroupBrightness,
  lightingStateLabel,
  lightingStateTone,
  QUICK_LIGHTING_GROUP_IDS,
} from './presentation';
import type { LightingControlActions, LightingGroup, LightingStatus } from './types';
import './lighting.css';

export interface LightingTileProps {
  status: LightingStatus | null;
  error: Error | null;
  refreshing: boolean;
  onOpen: () => void;
  controls: LightingControlActions;
}

function runControl(action: Promise<boolean>) {
  void action.catch(() => {});
}

function QuickRoomControl({
  group,
  controls,
}: {
  group: LightingGroup;
  controls: LightingControlActions;
}) {
  const brightness = Math.max(1, averageGroupBrightness(group) ?? 100);
  const available = group.lights.some((light) => light.available);
  const enabled = group.state === 'on';

  return (
    <div className={`lighting-quick-room is-${group.state}`}>
      <button
        type="button"
        className="lighting-quick-room__power"
        disabled={!available || controls.running}
        aria-pressed={group.state === 'mixed' ? 'mixed' : enabled}
        aria-label={`${group.label} power · ${lightingStateLabel(group.state)}`}
        onClick={() => runControl(controls.setPower(`group:${group.id}`, !enabled))}
      >
        <strong>{group.label}</strong>
        <small>{lightingStateLabel(group.state)}</small>
      </button>
      <LightingSlider
        label={`${group.label} brightness`}
        value={brightness}
        minimum={1}
        maximum={100}
        unit="%"
        disabled={!available || controls.running}
        className="lighting-quick-room__slider"
        onCommit={(value) => controls.setGroupBrightness(group.id, value)}
      />
    </div>
  );
}

export function LightingTile({ status, error, refreshing, onOpen, controls }: LightingTileProps) {
  if (!status) {
    return (
      <Tile
        icon="💡"
        title="Lighting"
        summary={error?.message ?? 'Reading room and light state…'}
        status={
          <StatusPill tone={error ? 'bad' : 'neutral'}>{error ? 'No data' : 'Loading'}</StatusPill>
        }
        tone={error ? 'bad' : 'neutral'}
        className="lighting-tile"
      >
        <button type="button" className="secondary-button" onClick={onOpen}>
          Open lighting details
        </button>
      </Tile>
    );
  }

  const unavailable = status.totalCount - status.availableCount;
  const summary = [`${status.onCount} on`, unavailable ? `${unavailable} unavailable` : null]
    .filter((item): item is string => item !== null)
    .join(' · ');
  const quickGroups = QUICK_LIGHTING_GROUP_IDS.map((id) =>
    status.groups.find((group) => group.id === id),
  ).filter((group) => group !== undefined);

  return (
    <Tile
      icon="💡"
      title="Lighting"
      summary={summary}
      status={
        <StatusPill tone={lightingStateTone(status.state)}>
          {lightingStateLabel(status.state)}
        </StatusPill>
      }
      tone={lightingStateTone(status.state)}
      className="lighting-tile"
    >
      <div className="lighting-tile__actions">
        <button
          type="button"
          className="lighting-master-button"
          disabled={status.availableCount === 0 || controls.running}
          aria-pressed={status.state === 'mixed' ? 'mixed' : status.state === 'on'}
          onClick={() => runControl(controls.setPower('all', status.state !== 'on'))}
        >
          {status.state === 'on' ? 'Turn all off' : 'Turn all on'}
        </button>
        <button
          type="button"
          className="secondary-button"
          aria-label="Open lighting details"
          onClick={onOpen}
        >
          Details
        </button>
      </div>
      <div className="lighting-quick-rooms" aria-label="Quick room controls">
        {quickGroups.map((group) => (
          <QuickRoomControl group={group} controls={controls} key={group.id} />
        ))}
      </div>
      {error && <p className="lighting-stale-error">Refresh failed · {error.message}</p>}
      <p className="lighting-control-status" role="status">
        {controls.running
          ? 'Applying lighting change…'
          : refreshing
            ? 'Refreshing lights…'
            : 'Live controls'}
      </p>
    </Tile>
  );
}
