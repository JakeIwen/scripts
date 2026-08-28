export type LightingAggregateState = 'on' | 'off' | 'mixed' | 'unknown';
export type LightDeviceState = 'on' | 'off' | 'unavailable' | 'unknown';

export interface LightingLight {
  entityId: string;
  label: string;
  state: LightDeviceState;
  available: boolean;
  brightness: number | null;
  colorMode: string | null;
  supportsHue: boolean;
  hue: number | null;
  supportsColorTemperature: boolean;
  colorTemperatureKelvin: number | null;
  minimumColorTemperatureKelvin: number | null;
  maximumColorTemperatureKelvin: number | null;
}

export interface LightingPowerSwitch {
  entityId: string;
  label: string;
  state: LightDeviceState;
  available: boolean;
}

export interface LightingGroup {
  id: string;
  label: string;
  state: LightingAggregateState;
  lights: LightingLight[];
  powerSwitch: LightingPowerSwitch | null;
}

export interface LightingStatus {
  state: LightingAggregateState;
  onCount: number;
  availableCount: number;
  totalCount: number;
  groups: LightingGroup[];
}

export interface LightingMutationResult {
  message: string;
  lighting: LightingStatus;
}

export interface LightingControlActions {
  running: boolean;
  setPower: (target: string, enabled: boolean) => Promise<boolean>;
  setBrightness: (entityId: string, brightness: number) => Promise<boolean>;
  setHue: (entityId: string, hue: number) => Promise<boolean>;
  setColorTemperature: (entityId: string, kelvin: number) => Promise<boolean>;
  setGroupBrightness: (groupId: string, brightness: number) => Promise<boolean>;
}
