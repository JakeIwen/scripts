import type { StatusTone } from '../../components/StatusPill';
import type {
  LightDeviceState,
  LightingAggregateState,
  LightingGroup,
  LightingLight,
} from './types';

export const QUICK_LIGHTING_GROUP_IDS = ['cab', 'rear', 'kitchen'] as const;

export function lightingStateLabel(state: LightingAggregateState | LightDeviceState): string {
  switch (state) {
    case 'on':
      return 'On';
    case 'off':
      return 'Off';
    case 'mixed':
      return 'Mixed';
    case 'unavailable':
      return 'Unavailable';
    case 'unknown':
      return 'No data';
  }
}

export function lightingStateTone(state: LightingAggregateState | LightDeviceState): StatusTone {
  if (state === 'on') return 'good';
  if (state === 'unavailable') return 'bad';
  return 'neutral';
}

export function averageGroupBrightness(group: LightingGroup): number | null {
  const levels = group.lights
    .filter((light) => light.available && light.brightness !== null)
    .map((light) => light.brightness as number);
  if (levels.length === 0) return null;
  return Math.round(levels.reduce((total, level) => total + level, 0) / levels.length);
}

export function lightColorDetails(light: LightingLight): string[] {
  const details: string[] = [];
  if (light.supportsHue) {
    details.push(light.hue === null ? 'Hue supported' : `Hue ${Math.round(light.hue)}°`);
  }
  if (light.supportsColorTemperature) {
    const range =
      light.minimumColorTemperatureKelvin !== null && light.maximumColorTemperatureKelvin !== null
        ? `${light.minimumColorTemperatureKelvin}–${light.maximumColorTemperatureKelvin} K`
        : 'range unavailable';
    details.push(
      light.colorTemperatureKelvin === null
        ? `Temperature supported · ${range}`
        : `${light.colorTemperatureKelvin} K · ${range}`,
    );
  }
  if (light.colorMode) details.push(`Mode ${light.colorMode.replaceAll('_', ' ')}`);
  return details;
}
