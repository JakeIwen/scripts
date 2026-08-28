import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  controlSonosTransport,
  selectSonosCoordinator,
  setSonosGrouping,
  setSonosGroupMuted,
  setSonosGroupVolume,
  setSonosSpeakerMuted,
  setSonosSpeakerVolume,
} from './api';
import { useSonosControls } from './controls';

vi.mock('./api', () => ({
  controlSonosTransport: vi.fn(),
  selectSonosCoordinator: vi.fn(),
  setSonosGrouping: vi.fn(),
  setSonosGroupMuted: vi.fn(),
  setSonosGroupVolume: vi.fn(),
  setSonosSpeakerMuted: vi.fn(),
  setSonosSpeakerVolume: vi.fn(),
}));

const transportMock = vi.mocked(controlSonosTransport);
const selectMock = vi.mocked(selectSonosCoordinator);
const groupingMock = vi.mocked(setSonosGrouping);
const groupMuteMock = vi.mocked(setSonosGroupMuted);
const groupVolumeMock = vi.mocked(setSonosGroupVolume);
const speakerMuteMock = vi.mocked(setSonosSpeakerMuted);
const speakerVolumeMock = vi.mocked(setSonosSpeakerVolume);

beforeEach(() => {
  const result = { message: 'Sonos updated' };
  transportMock.mockResolvedValue(result);
  selectMock.mockResolvedValue({ ...result, device: 'Rear' });
  groupingMock.mockResolvedValue(result);
  groupMuteMock.mockResolvedValue({ ...result, muted: true });
  groupVolumeMock.mockResolvedValue({ ...result, volume: 61 });
  speakerMuteMock.mockResolvedValue({ ...result, muted: true });
  speakerVolumeMock.mockResolvedValue({ ...result, volume: 34 });
});

describe('useSonosControls', () => {
  it('prevents duplicate clicks while one mutation is in flight', async () => {
    let finishMutation: ((value: { message: string }) => void) | undefined;
    transportMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishMutation = resolve;
        }),
    );
    const refresh = vi.fn().mockResolvedValue(null);
    const { result } = renderHook(() => useSonosControls(refresh));
    let first: Promise<unknown> | undefined;
    let duplicate: Promise<unknown> | undefined;

    act(() => {
      first = result.current.transport('next');
      duplicate = result.current.transport('next');
    });
    expect(transportMock).toHaveBeenCalledTimes(1);
    expect(result.current.running).toBe(true);

    await act(async () => {
      finishMutation?.({ message: 'Next Sonos track' });
      await first;
      await duplicate;
    });

    expect(refresh).toHaveBeenCalledTimes(1);
    expect(result.current.running).toBe(false);
  });

  it('does not retry a failed mutation and still refreshes authoritative status', async () => {
    speakerMuteMock.mockRejectedValueOnce(new Error('speaker did not answer'));
    const refresh = vi.fn().mockResolvedValue(null);
    const notify = vi.fn();
    const { result } = renderHook(() => useSonosControls(refresh, notify));
    let outcome: unknown;

    await act(async () => {
      outcome = await result.current.setSpeakerMuted('Rear', true);
    });

    expect(outcome).toBeNull();
    expect(speakerMuteMock).toHaveBeenCalledTimes(1);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(result.current.lastError).toBe('speaker did not answer');
    expect(notify).toHaveBeenCalledWith('speaker did not answer', 'error');
  });

  it('refreshes in finally after a successful mutation', async () => {
    const refresh = vi.fn().mockResolvedValue(null);
    const { result } = renderHook(() => useSonosControls(refresh));

    await act(async () => {
      await result.current.setGrouping('Solo', true);
    });

    expect(groupingMock).toHaveBeenCalledWith('Solo', true);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(result.current.lastMessage).toBe('Sonos updated');
  });
});
