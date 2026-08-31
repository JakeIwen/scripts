import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { getJson, postForm } from '../../api/client';
import type { LightingControlActions } from './types';
import { decodeLightingStatus } from './decoders';
import {
  LIGHTING_SHEET_POLL_INTERVAL_MS,
  LIGHTING_TILE_POLL_INTERVAL_MS,
  useLightingStatus,
} from './hooks';
import { LightingSheet } from './LightingSheet';
import { LightingTile } from './LightingTile';

vi.mock('../../api/client', () => ({ getJson: vi.fn(), postForm: vi.fn() }));

function lightingPayload(): Record<string, unknown> {
  return {
    ok: true,
    lighting: {
      state: 'mixed',
      on_count: 2,
      available_count: 5,
      total_count: 5,
      groups: [
        {
          id: 'cab',
          label: 'Cab',
          state: 'mixed',
          power_switch: null,
          lights: [
            {
              entity_id: 'light.wiz_front_driver',
              label: 'Driver',
              state: 'on',
              available: true,
              brightness: 50,
              color_mode: 'rgbww',
              supports_hue: true,
              hue: 28.5,
              supports_color_temperature: true,
              color_temp_kelvin: 3_200,
              min_color_temp_kelvin: 2_202,
              max_color_temp_kelvin: 6_535,
            },
            {
              entity_id: 'light.wiz_front_passenger',
              label: 'Passenger',
              state: 'off',
              available: true,
              brightness: null,
              color_mode: null,
              supports_hue: true,
              hue: null,
              supports_color_temperature: true,
              color_temp_kelvin: null,
              min_color_temp_kelvin: 2_202,
              max_color_temp_kelvin: 6_535,
            },
          ],
        },
        {
          id: 'rear',
          label: 'Rear',
          state: 'off',
          power_switch: null,
          lights: [
            {
              entity_id: 'light.wiz_dresser',
              label: 'Dresser',
              state: 'off',
              available: true,
              brightness: null,
              color_mode: null,
              supports_hue: false,
              hue: null,
              supports_color_temperature: false,
              color_temp_kelvin: null,
              min_color_temp_kelvin: null,
              max_color_temp_kelvin: null,
            },
          ],
        },
        {
          id: 'kitchen',
          label: 'Kitchen',
          state: 'on',
          power_switch: null,
          lights: [
            {
              entity_id: 'light.wiz_kitchen',
              label: 'Kitchen',
              state: 'on',
              available: true,
              brightness: 72,
              color_mode: 'color_temp',
              supports_hue: false,
              hue: null,
              supports_color_temperature: true,
              color_temp_kelvin: 3_000,
              min_color_temp_kelvin: 2_000,
              max_color_temp_kelvin: 7_000,
            },
          ],
        },
        {
          id: 'exterior',
          label: 'Exterior',
          state: 'off',
          power_switch: {
            entity_id: 'switch.ext_flood',
            label: 'Exterior power',
            state: 'on',
            available: true,
          },
          lights: [
            {
              entity_id: 'light.ext_led',
              label: 'Exterior LED',
              state: 'off',
              available: true,
              brightness: null,
              color_mode: null,
              supports_hue: false,
              hue: null,
              supports_color_temperature: false,
              color_temp_kelvin: null,
              min_color_temp_kelvin: null,
              max_color_temp_kelvin: null,
            },
          ],
        },
      ],
    },
  };
}

beforeAll(() => {
  if (!HTMLDialogElement.prototype.showModal) {
    HTMLDialogElement.prototype.showModal = function showModal() {
      this.setAttribute('open', '');
    };
  }
  if (!HTMLDialogElement.prototype.close) {
    HTMLDialogElement.prototype.close = function close() {
      this.removeAttribute('open');
      this.dispatchEvent(new Event('close'));
    };
  }
});

beforeEach(() => {
  vi.mocked(getJson).mockResolvedValue(lightingPayload());
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('lighting response decoder', () => {
  it('decodes room, brightness, power-switch, and supported color state', () => {
    const status = decodeLightingStatus(lightingPayload());
    const driver = status.groups[0]?.lights[0];

    expect(status).toMatchObject({
      state: 'mixed',
      onCount: 2,
      availableCount: 5,
      totalCount: 5,
    });
    expect(driver).toMatchObject({
      label: 'Driver',
      brightness: 50,
      supportsHue: true,
      hue: 28.5,
      supportsColorTemperature: true,
      colorTemperatureKelvin: 3_200,
    });
    expect(status.groups[3]?.powerSwitch?.entityId).toBe('switch.ext_flood');
  });

  it('rejects contradictory counts, availability, and color ranges', () => {
    const countMismatch = lightingPayload();
    (countMismatch.lighting as Record<string, unknown>).on_count = 4;
    expect(() => decodeLightingStatus(countMismatch)).toThrow(
      'lighting.on_count does not match returned lights',
    );

    const availabilityMismatch = lightingPayload();
    const lighting = availabilityMismatch.lighting as Record<string, unknown>;
    const groups = lighting.groups as Record<string, unknown>[];
    const cabLights = groups[0]!.lights as Record<string, unknown>[];
    cabLights[0]!.available = false;
    expect(() => decodeLightingStatus(availabilityMismatch)).toThrow(
      'lighting.groups[0].lights[0].available does not match',
    );

    const invalidTemperature = lightingPayload();
    const invalidGroups = (invalidTemperature.lighting as Record<string, unknown>).groups as Record<
      string,
      unknown
    >[];
    const invalidLights = invalidGroups[0]!.lights as Record<string, unknown>[];
    invalidLights[0]!.color_temp_kelvin = 7_000;
    expect(() => decodeLightingStatus(invalidTemperature)).toThrow(
      'color_temp_kelvin is outside its supported range',
    );
  });
});

describe('lighting polling', () => {
  it('uses one shared poller and speeds up only while the sheet is open', async () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(({ open }) => useLightingStatus(open), {
      initialProps: { open: false },
    });
    await act(async () => Promise.resolve());

    expect(result.current.data?.totalCount).toBe(5);
    expect(getJson).toHaveBeenCalledTimes(1);
    expect(getJson).toHaveBeenLastCalledWith('/api/lights', expect.any(AbortSignal));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(LIGHTING_TILE_POLL_INTERVAL_MS);
    });
    expect(getJson).toHaveBeenCalledTimes(2);

    rerender({ open: true });
    await act(async () => Promise.resolve());
    expect(getJson).toHaveBeenCalledTimes(3);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(LIGHTING_SHEET_POLL_INTERVAL_MS);
    });
    expect(getJson).toHaveBeenCalledTimes(4);
  });
});

describe('lighting views', () => {
  function controls(overrides: Partial<LightingControlActions> = {}): LightingControlActions {
    return {
      running: false,
      setPower: vi.fn().mockResolvedValue(true),
      setBrightness: vi.fn().mockResolvedValue(true),
      setHue: vi.fn().mockResolvedValue(true),
      setColorTemperature: vi.fn().mockResolvedValue(true),
      setGroupBrightness: vi.fn().mockResolvedValue(true),
      ...overrides,
    };
  }

  it('shows the three quick rooms with live power and brightness controls', () => {
    const status = decodeLightingStatus(lightingPayload());
    const onOpen = vi.fn();
    const actions = controls();
    render(
      <LightingTile
        status={status}
        error={null}
        refreshing={false}
        onOpen={onOpen}
        controls={actions}
      />,
    );

    expect(screen.getByText('Cab')).toBeInTheDocument();
    expect(screen.getByText('Rear')).toBeInTheDocument();
    expect(screen.getByRole('slider', { name: 'Kitchen brightness' })).toHaveValue('72');
    fireEvent.click(screen.getByRole('button', { name: /^Cab power/ }));
    expect(actions.setPower).toHaveBeenCalledWith('group:cab', true);
    fireEvent.click(screen.getByRole('button', { name: 'Open lighting controls' }));
    expect(onOpen).toHaveBeenCalledOnce();
  });

  it('shows supported color data and disables all mutations during a single-flight action', () => {
    const status = decodeLightingStatus(lightingPayload());
    const onRefresh = vi.fn().mockResolvedValue(status);
    render(
      <LightingSheet
        open
        onClose={vi.fn()}
        status={status}
        error={null}
        refreshing={false}
        onRefresh={onRefresh}
        controls={controls({ running: true })}
      />,
    );

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText(/Hue 29°/)).toBeInTheDocument();
    expect(screen.getByText(/3200 K · 2202–6535 K/)).toBeInTheDocument();
    expect(screen.getByText('Exterior power')).toBeInTheDocument();
    expect(screen.getByRole('slider', { name: 'Driver hue' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Turn all on' })).toBeDisabled();

    expect(screen.getByRole('button', { name: 'Refresh status' })).toBeDisabled();
  });

  it('disables unavailable lights and omits unsupported color controls', () => {
    const payload = lightingPayload();
    const lighting = payload.lighting as Record<string, unknown>;
    lighting.available_count = 4;
    const groups = lighting.groups as Record<string, unknown>[];
    const cabLights = groups[0]!.lights as Record<string, unknown>[];
    cabLights[1]!.state = 'unavailable';
    cabLights[1]!.available = false;
    const status = decodeLightingStatus(payload);

    render(
      <LightingSheet
        open
        onClose={vi.fn()}
        status={status}
        error={null}
        refreshing={false}
        onRefresh={vi.fn().mockResolvedValue(status)}
        controls={controls()}
      />,
    );

    expect(screen.getByRole('button', { name: 'Passenger power' })).toBeDisabled();
    expect(screen.getByRole('slider', { name: 'Passenger brightness' })).toBeDisabled();
    expect(screen.getByRole('slider', { name: 'Passenger hue' })).toBeDisabled();
    expect(screen.getByRole('slider', { name: 'Passenger color temperature' })).toBeDisabled();
    expect(screen.queryByRole('slider', { name: 'Dresser hue' })).not.toBeInTheDocument();
    expect(
      screen.queryByRole('slider', { name: 'Dresser color temperature' }),
    ).not.toBeInTheDocument();
  });
});
