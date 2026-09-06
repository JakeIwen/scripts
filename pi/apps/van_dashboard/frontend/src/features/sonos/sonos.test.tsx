import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { getJson } from '../../api/client';
import type { SonosControls } from './controls';
import { decodeSonosStatus } from './decoders';
import { SONOS_POLL_INTERVAL_MS, useSonosStatus } from './hooks';
import { projectTrackProgress } from './progress';
import { SonosSheet } from './SonosSheet';
import { SonosTile } from './SonosTile';

vi.mock('../../api/client', () => ({ getJson: vi.fn(), postForm: vi.fn() }));

function sonosPayload(): Record<string, unknown> {
  return {
    ok: true,
    coordinator: 'Front',
    group: { volume: 61, muted: false },
    now_playing: {
      title: 'Orange Juice',
      artist: 'Stanley Brinks and The Wave Pictures',
      album: 'Gin',
      position: '0:01:23',
      duration: '0:03:45',
      transport_state: 'PLAYING',
      album_art: '/api/speakers/art/0123456789abcdef',
    },
    speakers: [
      {
        name: 'Front',
        volume: 28,
        muted: false,
        grouped: true,
        coordinator: true,
        group_coordinator: 'Front',
      },
      {
        name: 'Rear',
        volume: 34,
        muted: true,
        grouped: true,
        coordinator: false,
        group_coordinator: 'Front',
      },
      {
        name: 'Solo',
        volume: 19,
        muted: false,
        grouped: false,
        coordinator: false,
        group_coordinator: 'Solo',
      },
    ],
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
  vi.mocked(getJson).mockResolvedValue(sonosPayload());
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

function controlMocks(overrides: Partial<SonosControls> = {}): SonosControls {
  const result = { message: 'updated' };
  return {
    running: false,
    activeAction: null,
    lastMessage: null,
    lastError: null,
    transport: vi.fn().mockResolvedValue(result),
    selectCoordinator: vi.fn().mockResolvedValue(result),
    setGrouping: vi.fn().mockResolvedValue(result),
    setSpeakerVolume: vi.fn().mockResolvedValue(result),
    setSpeakerMuted: vi.fn().mockResolvedValue(result),
    setGroupVolume: vi.fn().mockResolvedValue(result),
    setGroupMuted: vi.fn().mockResolvedValue(result),
    ...overrides,
  };
}

describe('Sonos response decoder', () => {
  it('decodes playback clocks and the active group', () => {
    const status = decodeSonosStatus(sonosPayload());

    expect(status.coordinator).toBe('Front');
    expect(status.nowPlaying.positionSeconds).toBe(83);
    expect(status.nowPlaying.durationSeconds).toBe(225);
    expect(status.speakers.filter((speaker) => speaker.grouped)).toHaveLength(2);
    expect(status.group).toEqual({ volume: 61, muted: false });
  });

  it('treats the Sonos idle clock sentinel as unavailable progress', () => {
    const payload = sonosPayload();
    const nowPlaying = payload.now_playing as Record<string, unknown>;
    nowPlaying.title = 'Nothing playing';
    nowPlaying.position = 'NOT_IMPLEMENTED';
    nowPlaying.duration = 'NOT_IMPLEMENTED';
    nowPlaying.transport_state = 'PAUSED_PLAYBACK';
    nowPlaying.album_art = null;

    const status = decodeSonosStatus(payload);

    expect(status.nowPlaying.positionSeconds).toBeNull();
    expect(status.nowPlaying.durationSeconds).toBeNull();
    expect(projectTrackProgress(status.nowPlaying, 1_000, 6_000)).toBeNull();
  });

  it('rejects unsafe art paths, invalid clocks, and mismatched coordinators', () => {
    const unsafeArt = sonosPayload();
    (unsafeArt.now_playing as Record<string, unknown>).album_art = 'http://speaker/art.jpg';
    expect(() => decodeSonosStatus(unsafeArt)).toThrow(
      'now_playing.album_art must be a dashboard Sonos-art path',
    );

    const badClock = sonosPayload();
    (badClock.now_playing as Record<string, unknown>).position = '1:99';
    expect(() => decodeSonosStatus(badClock)).toThrow(
      'now_playing.position contains an invalid clock value',
    );

    const mismatched = sonosPayload();
    (mismatched.speakers as Record<string, unknown>[])[0]!.coordinator = false;
    expect(() => decodeSonosStatus(mismatched)).toThrow(
      'Sonos coordinator flags do not match response.coordinator',
    );
  });
});

describe('Sonos polling and local progress', () => {
  it('uses one bounded ten-second GET poller', async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useSonosStatus());
    await act(async () => Promise.resolve());

    expect(result.current.data?.coordinator).toBe('Front');
    expect(getJson).toHaveBeenCalledTimes(1);
    expect(getJson).toHaveBeenLastCalledWith('/api/speakers', expect.any(AbortSignal));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(SONOS_POLL_INTERVAL_MS);
    });
    expect(getJson).toHaveBeenCalledTimes(2);
  });

  it('advances playing position locally and clamps at track duration', () => {
    const track = decodeSonosStatus(sonosPayload()).nowPlaying;
    expect(projectTrackProgress(track, 1_000, 6_000)).toMatchObject({
      positionSeconds: 88,
      durationSeconds: 225,
      label: '1:28 of 3:45',
    });
    expect(projectTrackProgress(track, 1_000, 999_000)?.positionSeconds).toBe(225);
  });
});

describe('Sonos views', () => {
  it('keeps decoder details out of the unavailable tile', () => {
    render(
      <SonosTile
        status={null}
        error={new Error('now_playing.position must use H:MM:SS or MM:SS clock text')}
        refreshing={false}
        progress={null}
        controls={controlMocks()}
        onOpen={vi.fn()}
      />,
    );

    expect(screen.getByText('Could not read current Sonos status')).toBeInTheDocument();
    expect(screen.queryByText(/H:MM:SS/)).not.toBeInTheDocument();
  });

  it('shows playback and opens the control sheet', () => {
    const status = decodeSonosStatus(sonosPayload());
    const progress = projectTrackProgress(status.nowPlaying, 1_000, 6_000);
    const onOpen = vi.fn();
    const controls = controlMocks();
    render(
      <SonosTile
        status={status}
        error={null}
        refreshing={false}
        progress={progress}
        controls={controls}
        onOpen={onOpen}
      />,
    );

    expect(screen.getByText('Orange Juice')).toBeInTheDocument();
    expect(screen.getByText('Front · 2/3')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Previous track' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Pause' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Next track' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Next track' }));
    expect(controls.transport).toHaveBeenCalledWith('next');
    fireEvent.click(screen.getByRole('button', { name: 'Open Sonos details' }));
    expect(onOpen).toHaveBeenCalledOnce();
  });

  it('announces playback refreshes without adding a visible tile row', () => {
    const status = decodeSonosStatus(sonosPayload());
    render(
      <SonosTile
        status={status}
        error={null}
        refreshing
        progress={projectTrackProgress(status.nowPlaying, 1_000, 6_000)}
        controls={controlMocks()}
        onOpen={vi.fn()}
      />,
    );

    const announcement = screen.getByText('Refreshing playback…');
    expect(announcement).toHaveClass('visually-hidden');
    expect(screen.getByRole('group', { name: 'Sonos playback controls' })).toBeVisible();
  });

  it('renders functional transport, grouping, mute, selection, and volume controls', () => {
    const status = decodeSonosStatus(sonosPayload());
    const progress = projectTrackProgress(status.nowPlaying, 1_000, 6_000);
    const onRefresh = vi.fn().mockResolvedValue(status);
    const controls = controlMocks();
    render(
      <SonosSheet
        open
        onClose={vi.fn()}
        status={status}
        error={null}
        refreshing={false}
        progress={progress}
        onRefresh={onRefresh}
        controls={controls}
      />,
    );

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('Active coordinator')).toBeInTheDocument();
    expect(screen.getByText('Separate group · Solo')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Previous track' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Pause' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Next track' })).toBeEnabled();
    expect(screen.getByRole('checkbox', { name: 'Group Front with Front' })).toBeDisabled();
    expect(screen.getByRole('checkbox', { name: 'Group Solo with Front' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Mute Front' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Select group' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Active group' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'Previous track' }));
    expect(controls.transport).toHaveBeenCalledWith('previous');
    fireEvent.click(screen.getByRole('button', { name: 'Mute Front' }));
    expect(controls.setSpeakerMuted).toHaveBeenCalledWith('Front', true);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Group Solo with Front' }));
    expect(controls.setGrouping).toHaveBeenCalledWith('Solo', true);
    fireEvent.click(screen.getByRole('button', { name: 'Select group' }));
    expect(controls.selectCoordinator).toHaveBeenCalledWith('Solo');
    fireEvent.change(screen.getByRole('slider', { name: 'Rear volume' }), {
      target: { value: '40' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Set Rear volume' }));
    expect(controls.setSpeakerVolume).toHaveBeenCalledWith('Rear', 40);
    fireEvent.click(screen.getByRole('button', { name: 'Mute group' }));
    expect(controls.setGroupMuted).toHaveBeenCalledWith(true);

    fireEvent.click(screen.getByRole('button', { name: 'Refresh status' }));
    expect(onRefresh).toHaveBeenCalledOnce();
  });

  it('disables unknown or busy controls', () => {
    const status = decodeSonosStatus(sonosPayload());
    status.group.volume = null;
    status.group.muted = null;
    const rear = status.speakers.find((speaker) => speaker.name === 'Rear');
    if (!rear) throw new Error('missing Rear fixture');
    rear.volume = null;
    rear.muted = null;
    const controls = controlMocks({ running: true, activeAction: 'transport:next' });

    render(
      <SonosSheet
        open
        onClose={vi.fn()}
        status={status}
        error={null}
        refreshing={false}
        progress={null}
        onRefresh={vi.fn().mockResolvedValue(status)}
        controls={controls}
      />,
    );

    expect(screen.getByRole('button', { name: 'Next track' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Mute group' })).toBeDisabled();
    expect(screen.getByRole('slider', { name: 'Group volume' })).toBeDisabled();
    expect(screen.getByRole('slider', { name: 'Rear volume' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Mute Rear' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Refresh status' })).toBeDisabled();
  });
});
