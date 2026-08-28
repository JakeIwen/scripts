import { BottomSheet } from '../../components/BottomSheet';
import { StatusPill } from '../../components/StatusPill';
import { LightingSlider } from './LightingSlider';
import {
  averageGroupBrightness,
  lightColorDetails,
  lightingStateLabel,
  lightingStateTone,
} from './presentation';
import type { LightingControlActions, LightingGroup, LightingLight, LightingStatus } from './types';
import './lighting.css';

export interface LightingSheetProps {
  open: boolean;
  onClose: () => void;
  status: LightingStatus | null;
  error: Error | null;
  refreshing: boolean;
  onRefresh: () => Promise<LightingStatus | null>;
  controls: LightingControlActions;
}

function runControl(action: Promise<boolean>) {
  void action.catch(() => {});
}

function backendTemperatureRange(light: LightingLight): {
  minimum: number;
  maximum: number;
  supported: boolean;
} {
  const minimum = Math.max(2_000, light.minimumColorTemperatureKelvin ?? 2_000);
  const maximum = Math.min(7_000, light.maximumColorTemperatureKelvin ?? 7_000);
  return { minimum, maximum, supported: minimum <= maximum };
}

function LightRow({ light, controls }: { light: LightingLight; controls: LightingControlActions }) {
  const colors = lightColorDetails(light);
  const enabled = light.state === 'on';
  const temperature = backendTemperatureRange(light);
  const temperatureValue = Math.max(
    temperature.minimum,
    Math.min(temperature.maximum, light.colorTemperatureKelvin ?? 3_500),
  );
  return (
    <article className="lighting-light">
      <span className={`lighting-light__dot is-${light.state}`} aria-hidden="true" />
      <div className="lighting-light__identity">
        <strong>{light.label}</strong>
        <code>{light.entityId}</code>
      </div>
      <button
        type="button"
        className="lighting-light__power"
        disabled={!light.available || controls.running}
        aria-pressed={enabled}
        aria-label={`${light.label} power`}
        onClick={() => runControl(controls.setPower(light.entityId, !enabled))}
      >
        {lightingStateLabel(light.state)}
      </button>
      <div className="lighting-light__controls">
        <LightingSlider
          label={`${light.label} brightness`}
          value={Math.max(1, light.brightness ?? 100)}
          minimum={1}
          maximum={100}
          unit="%"
          disabled={!light.available || controls.running}
          onCommit={(value) => controls.setBrightness(light.entityId, value)}
        />
        {light.supportsHue && (
          <LightingSlider
            label={`${light.label} hue`}
            value={Math.round(light.hue ?? 0)}
            minimum={0}
            maximum={360}
            unit="°"
            disabled={!light.available || controls.running}
            className="lighting-slider-control--hue"
            onCommit={(value) => controls.setHue(light.entityId, value)}
          />
        )}
        {light.supportsColorTemperature && (
          <LightingSlider
            label={`${light.label} color temperature${temperature.supported ? '' : ' (unsupported range)'}`}
            value={temperatureValue}
            minimum={temperature.minimum}
            maximum={temperature.supported ? temperature.maximum : temperature.minimum}
            unit=" K"
            disabled={!light.available || !temperature.supported || controls.running}
            className="lighting-slider-control--temperature"
            onCommit={(value) => controls.setColorTemperature(light.entityId, value)}
          />
        )}
      </div>
      {colors.length > 0 && (
        <div className="lighting-light__colors">
          {light.hue !== null && (
            <span
              className="lighting-hue-swatch"
              style={{ backgroundColor: `hsl(${light.hue} 70% 55%)` }}
              aria-hidden="true"
            />
          )}
          <span>{colors.join(' · ')}</span>
        </div>
      )}
    </article>
  );
}

function LightingGroupCard({
  group,
  controls,
}: {
  group: LightingGroup;
  controls: LightingControlActions;
}) {
  const brightness = averageGroupBrightness(group);
  const available = group.lights.some((light) => light.available);
  const enabled = group.state === 'on';
  return (
    <section className="panel-card lighting-group-card">
      <header>
        <div>
          <h3>{group.label}</h3>
          <p>
            {group.lights.length} {group.lights.length === 1 ? 'light' : 'lights'}
            {brightness === null ? '' : ` · average ${brightness}%`}
          </p>
        </div>
        <StatusPill tone={lightingStateTone(group.state)}>
          {lightingStateLabel(group.state)}
        </StatusPill>
        <button
          type="button"
          className="lighting-group-power"
          disabled={!available || controls.running}
          aria-pressed={group.state === 'mixed' ? 'mixed' : enabled}
          aria-label={`${enabled ? 'Turn off' : 'Turn on'} ${group.label} room`}
          onClick={() => runControl(controls.setPower(`group:${group.id}`, !enabled))}
        >
          {enabled ? 'Turn room off' : 'Turn room on'}
        </button>
      </header>
      {group.powerSwitch && (
        <div className="lighting-power-switch">
          <span>{group.powerSwitch.label}</span>
          <button
            type="button"
            disabled={!group.powerSwitch.available || controls.running}
            aria-pressed={group.powerSwitch.state === 'on'}
            aria-label={group.powerSwitch.label}
            onClick={() =>
              runControl(
                controls.setPower(group.powerSwitch!.entityId, group.powerSwitch!.state !== 'on'),
              )
            }
          >
            {lightingStateLabel(group.powerSwitch.state)}
          </button>
        </div>
      )}
      <LightingSlider
        label={`${group.label} room brightness`}
        value={Math.max(1, brightness ?? 100)}
        minimum={1}
        maximum={100}
        unit="%"
        disabled={!available || controls.running}
        className="lighting-group-brightness"
        onCommit={(value) => controls.setGroupBrightness(group.id, value)}
      />
      <div className="lighting-light-list">
        {group.lights.map((light) => (
          <LightRow light={light} controls={controls} key={light.entityId} />
        ))}
      </div>
    </section>
  );
}

export function LightingSheet({
  open,
  onClose,
  status,
  error,
  refreshing,
  onRefresh,
  controls,
}: LightingSheetProps) {
  return (
    <BottomSheet
      open={open}
      title="Lighting"
      description={
        'Authoritative Home Assistant state for configured rooms, lights, and ' + 'power switches.'
      }
      onClose={onClose}
    >
      <div className="lighting-sheet__toolbar">
        <aside className="lighting-live-panel">
          <strong>Live lighting controls</strong>
          <span>Changes are serialized and reconciled with Home Assistant state.</span>
        </aside>
        <div className="lighting-sheet__actions">
          <button
            type="button"
            className="secondary-button"
            disabled={refreshing || controls.running || !open}
            onClick={() => void onRefresh()}
          >
            {refreshing ? 'Refreshing…' : 'Refresh status'}
          </button>
        </div>
      </div>

      {error && <p className="error-message">{error.message}</p>}
      {!status ? (
        <p className="lighting-sheet__empty">
          {error ? 'Lighting state is unavailable.' : 'Reading configured lights…'}
        </p>
      ) : (
        <div className="section-stack">
          <section className="lighting-overview panel-card">
            <div>
              <span>All configured lights</span>
              <strong>
                {status.onCount} on · {status.availableCount}/{status.totalCount} available
              </strong>
            </div>
            <StatusPill tone={lightingStateTone(status.state)}>
              {lightingStateLabel(status.state)}
            </StatusPill>
            <button
              type="button"
              className="lighting-master-button"
              disabled={status.availableCount === 0 || controls.running}
              aria-pressed={status.state === 'mixed' ? 'mixed' : status.state === 'on'}
              onClick={() => runControl(controls.setPower('all', status.state !== 'on'))}
            >
              {status.state === 'on' ? 'Turn all off' : 'Turn all on'}
            </button>
          </section>
          {status.groups.map((group) => (
            <LightingGroupCard group={group} controls={controls} key={group.id} />
          ))}
        </div>
      )}
    </BottomSheet>
  );
}
