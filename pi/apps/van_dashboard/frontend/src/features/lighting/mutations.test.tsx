import { act, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { getJson, postForm } from '../../api/client';
import { ToastProvider } from '../../components/ToastProvider';
import { setLightBrightness, setLightColorTemperature, setLightHue, setLightingPower } from './api';
import { decodeLightingStatus } from './decoders';
import { LightingSlider } from './LightingSlider';
import { useLightingControls } from './useLightingControls';

vi.mock('../../api/client', () => ({ getJson: vi.fn(), postForm: vi.fn() }));

function controlPayload() {
  return {
    ok: true,
    message: 'Lighting updated',
    lighting: {
      state: 'mixed',
      on_count: 1,
      available_count: 2,
      total_count: 2,
      groups: [
        {
          id: 'cab',
          label: 'Cab',
          state: 'mixed',
          power_switch: null,
          lights: [
            {
              entity_id: 'light.cab_driver',
              label: 'Driver',
              state: 'on',
              available: true,
              brightness: 50,
              color_mode: 'rgb',
              supports_hue: true,
              hue: 20,
              supports_color_temperature: true,
              color_temp_kelvin: 3_000,
              min_color_temp_kelvin: 2_200,
              max_color_temp_kelvin: 6_500,
            },
            {
              entity_id: 'light.cab_passenger',
              label: 'Passenger',
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

function wrapper({ children }: { children: React.ReactNode }) {
  return <ToastProvider>{children}</ToastProvider>;
}

beforeEach(() => {
  vi.mocked(postForm).mockResolvedValue(controlPayload());
  vi.mocked(getJson).mockResolvedValue(controlPayload());
});

afterEach(() => {
  vi.useRealTimers();
});

describe('lighting mutation API', () => {
  it('uses the exact URL-encoded endpoint fields', async () => {
    await setLightingPower('group:cab', true);
    await setLightBrightness('light.cab_driver', 64);
    await setLightHue('light.cab_driver', 180);
    await setLightColorTemperature('light.cab_driver', 3_500);

    expect(postForm).toHaveBeenNthCalledWith(1, '/api/lights/power', {
      target: 'group:cab',
      value: 'true',
    });
    expect(postForm).toHaveBeenNthCalledWith(2, '/api/lights/brightness', {
      entity: 'light.cab_driver',
      brightness: 64,
    });
    expect(postForm).toHaveBeenNthCalledWith(3, '/api/lights/hue', {
      entity: 'light.cab_driver',
      hue: 180,
    });
    expect(postForm).toHaveBeenNthCalledWith(4, '/api/lights/color-temperature', {
      entity: 'light.cab_driver',
      kelvin: 3_500,
    });
  });
});

describe('useLightingControls', () => {
  it('publishes the authoritative returned state and refreshes after failure', async () => {
    const status = decodeLightingStatus(controlPayload());
    const onAuthoritativeStatus = vi.fn();
    const { result } = renderHook(() => useLightingControls({ status, onAuthoritativeStatus }), {
      wrapper,
    });

    await act(async () => {
      await expect(result.current.setPower('all', true)).resolves.toBe(true);
    });
    expect(onAuthoritativeStatus).toHaveBeenLastCalledWith(status);

    vi.mocked(postForm).mockRejectedValueOnce(new Error('Home Assistant unavailable'));
    await act(async () => {
      await expect(result.current.setBrightness('light.cab_driver', 70)).rejects.toThrow(
        'Home Assistant unavailable',
      );
    });
    expect(getJson).toHaveBeenCalledWith('/api/lights', expect.any(AbortSignal));
    expect(onAuthoritativeStatus).toHaveBeenLastCalledWith(status);
  });

  it('runs room brightness sequentially and performs a final GET refresh', async () => {
    const status = decodeLightingStatus(controlPayload());
    const onAuthoritativeStatus = vi.fn();
    const { result } = renderHook(() => useLightingControls({ status, onAuthoritativeStatus }), {
      wrapper,
    });

    await act(async () => {
      await expect(result.current.setGroupBrightness('cab', 66)).resolves.toBe(true);
    });

    expect(postForm).toHaveBeenCalledTimes(2);
    expect(postForm).toHaveBeenNthCalledWith(1, '/api/lights/brightness', {
      entity: 'light.cab_driver',
      brightness: 66,
    });
    expect(postForm).toHaveBeenNthCalledWith(2, '/api/lights/brightness', {
      entity: 'light.cab_passenger',
      brightness: 66,
    });
    expect(getJson).toHaveBeenCalledTimes(1);
    expect(getJson).toHaveBeenCalledWith('/api/lights', expect.any(AbortSignal));
  });

  it('still performs the final room refresh after a partial sequential failure', async () => {
    const status = decodeLightingStatus(controlPayload());
    const onAuthoritativeStatus = vi.fn();
    vi.mocked(postForm)
      .mockResolvedValueOnce(controlPayload())
      .mockRejectedValueOnce(new Error('Passenger light unavailable'));
    const { result } = renderHook(() => useLightingControls({ status, onAuthoritativeStatus }), {
      wrapper,
    });

    await act(async () => {
      await expect(result.current.setGroupBrightness('cab', 55)).rejects.toThrow(
        'Passenger light unavailable',
      );
    });

    expect(postForm).toHaveBeenCalledTimes(2);
    expect(getJson).toHaveBeenCalledTimes(1);
    expect(onAuthoritativeStatus).toHaveBeenCalledWith(status);
  });

  it('rejects a second mutation while the first is in flight', async () => {
    const status = decodeLightingStatus(controlPayload());
    let release: ((payload: unknown) => void) | undefined;
    vi.mocked(postForm).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );
    const { result } = renderHook(
      () => useLightingControls({ status, onAuthoritativeStatus: vi.fn() }),
      { wrapper },
    );

    let first!: Promise<boolean>;
    let second!: Promise<boolean>;
    act(() => {
      first = result.current.setPower('all', true);
      second = result.current.setPower('all', false);
    });
    await expect(second).resolves.toBe(false);
    expect(postForm).toHaveBeenCalledTimes(1);

    await act(async () => {
      release?.(controlPayload());
      await first;
    });
  });
});

describe('LightingSlider', () => {
  it('updates locally and commits once after the idle debounce', async () => {
    vi.useFakeTimers();
    const onCommit = vi.fn().mockResolvedValue(true);
    render(
      <LightingSlider
        label="Cab brightness"
        value={40}
        minimum={1}
        maximum={100}
        unit="%"
        disabled={false}
        onCommit={onCommit}
      />,
    );

    fireEvent.input(screen.getByRole('slider', { name: 'Cab brightness' }), {
      target: { value: '67' },
    });
    expect(screen.getByText('67%')).toBeInTheDocument();
    expect(onCommit).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(499);
    });
    expect(onCommit).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(onCommit).toHaveBeenCalledOnce();
    expect(onCommit).toHaveBeenCalledWith(67);
  });

  it('commits immediately on pointer release and cancels the pending debounce', async () => {
    vi.useFakeTimers();
    const onCommit = vi.fn().mockResolvedValue(true);
    render(
      <LightingSlider
        label="Rear brightness"
        value={30}
        minimum={1}
        maximum={100}
        unit="%"
        disabled={false}
        onCommit={onCommit}
      />,
    );

    const slider = screen.getByRole('slider', { name: 'Rear brightness' });
    fireEvent.input(slider, { target: { value: '48' } });
    fireEvent.pointerUp(slider);
    await act(async () => Promise.resolve());
    expect(onCommit).toHaveBeenCalledOnce();
    expect(onCommit).toHaveBeenCalledWith(48);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    expect(onCommit).toHaveBeenCalledOnce();
  });
});
