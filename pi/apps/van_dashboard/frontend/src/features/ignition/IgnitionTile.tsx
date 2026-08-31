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

export function IgnitionTile({ status, error, remainingSeconds, onOpen }: IgnitionTileProps) {
  if (!status) {
    const unavailable = Boolean(error);
    return (
      <Tile
        icon="🔑"
        title="Ignition Monitor"
        summary={error?.message ?? 'Checking monitoring service…'}
        status={
          <StatusPill tone="neutral" dot={false}>
            No data
          </StatusPill>
        }
        tone="neutral"
        onClick={onOpen}
        ariaLabel="Open ignition monitor details"
        className="ignition-tile"
      >
        <KeyValueList
          items={[
            { label: 'Service', value: unavailable ? 'Unavailable' : 'Checking…' },
            { label: 'Monitoring', value: unavailable ? 'Unavailable' : 'Checking…' },
          ]}
          className="ignition-tile__details"
        />
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
      label: 'Monitoring',
      value: status.monitor.active ? 'Active' : `Paused · ${remainingLabel} left`,
    },
  ];

  return (
    <Tile
      icon="🔑"
      title="Ignition Monitor"
      summary={presentation.summary}
      status={
        <StatusPill tone={presentation.tone} dot={false}>
          {presentation.label}
        </StatusPill>
      }
      tone={presentation.tone}
      onClick={onOpen}
      ariaLabel="Open ignition monitor details"
      className="ignition-tile"
    >
      <KeyValueList items={items} className="ignition-tile__details" />
    </Tile>
  );
}
