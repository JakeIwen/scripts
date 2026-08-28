import { KeyValueList } from '../../components/KeyValueList';
import { StatusPill } from '../../components/StatusPill';
import { Tile } from '../../components/Tile';
import { formatIgnitionDuration } from './countdown';
import { describeIgnitionMonitor } from './presentation';
import type { IgnitionMonitorStatus } from './types';
import './ignition.css';

export interface IgnitionTileProps {
  status: IgnitionMonitorStatus | null;
  error: Error | null;
  refreshing: boolean;
  remainingSeconds: number | null;
  onOpen: () => void;
}

export function IgnitionTile({
  status,
  error,
  refreshing,
  remainingSeconds,
  onOpen,
}: IgnitionTileProps) {
  if (!status) {
    return (
      <Tile
        icon="🔑"
        title="Ignition Monitor"
        summary={error?.message ?? 'Checking service and pause state…'}
        status={
          <StatusPill tone={error ? 'bad' : 'neutral'}>{error ? 'No data' : 'Loading'}</StatusPill>
        }
        tone={error ? 'bad' : 'neutral'}
        onClick={onOpen}
        ariaLabel="Open ignition monitor details"
        className="ignition-tile"
      >
        <p className="ignition-control-note">Pause controls are available in details.</p>
      </Tile>
    );
  }

  const remainingLabel = formatIgnitionDuration(remainingSeconds);
  const presentation = describeIgnitionMonitor(status, remainingLabel);
  const items = [
    {
      label: 'Service',
      value: status.service.running
        ? `Running · ${status.service.enabled ? 'starts at boot' : 'not enabled at boot'}`
        : `${status.service.activeState} · ${status.service.subState}`,
    },
    {
      label: 'Ignition actions',
      value: status.monitor.active ? 'Enabled' : `Paused · ${remainingLabel} left`,
    },
  ];

  return (
    <Tile
      icon="🔑"
      title="Ignition Monitor"
      summary={presentation.summary}
      status={<StatusPill tone={presentation.tone}>{presentation.label}</StatusPill>}
      tone={presentation.tone}
      onClick={onOpen}
      ariaLabel="Open ignition monitor details"
      className="ignition-tile"
    >
      <KeyValueList items={items} className="ignition-tile__details" />
      {error && <p className="ignition-stale-error">Refresh failed · {error.message}</p>}
      <p className="ignition-control-note">
        {refreshing ? 'Refreshing monitor…' : 'Pause controls are available in details.'}
      </p>
    </Tile>
  );
}
