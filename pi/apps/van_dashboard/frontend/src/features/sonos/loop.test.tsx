import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { deferred } from '../../test/deferred';
import type { SonosControls } from './controls';
import { loopProblem, parseLoopTime } from './loop';
import { SonosPlayback } from './SonosPlayback';
import type { SonosLoop, SonosStatus, SonosTrackProgress } from './types';

afterEach(cleanup);

function status(loop: SonosLoop = { active: false, notice: null }): SonosStatus {
  return {
    coordinator: 'Front',
    group: { volume: 61, muted: false },
    nowPlaying: {
      title: 'Orange Juice',
      artist: 'Stanley Brinks',
      album: 'Gin',
      positionSeconds: 83,
      durationSeconds: 225,
      transportState: 'PLAYING',
      albumArt: null,
    },
    speakers: [],
    loop,
  };
}

const progress: SonosTrackProgress = {
  positionSeconds: 83.6,
  durationSeconds: 225,
  label: '1:23 of 3:45',
};

function controlMocks(overrides: Partial<SonosControls> = {}): SonosControls {
  const result = { message: 'updated' };
  return {
    running: false,
    activeAction: null,
    lastMessage: null,
    lastError: null,
    transport: vi.fn().mockResolvedValue(result),
    seek: vi.fn().mockResolvedValue(result),
    seekTo: vi.fn().mockResolvedValue(result),
    startLoop: vi.fn().mockResolvedValue(result),
    stopLoop: vi.fn().mockResolvedValue(result),
    selectCoordinator: vi.fn().mockResolvedValue(result),
    setGrouping: vi.fn().mockResolvedValue(result),
    setSpeakerVolume: vi.fn().mockResolvedValue(result),
    setSpeakerMuted: vi.fn().mockResolvedValue(result),
    setGroupVolume: vi.fn().mockResolvedValue(result),
    setGroupMuted: vi.fn().mockResolvedValue(result),
    ...overrides,
  };
}

describe('loop time entry', () => {
  it('accepts seconds, M:SS and H:MM:SS and rejects malformed clocks', () => {
    expect(parseLoopTime('83')).toBe(83);
    expect(parseLoopTime(' 1:23 ')).toBe(83);
    expect(parseLoopTime('1:02:03')).toBe(3723);
    expect(parseLoopTime('1:75')).toBeNull();
    expect(parseLoopTime('1:2:3:4')).toBeNull();
    expect(parseLoopTime('abc')).toBeNull();
  });

  it('explains ranges the Pi would reject', () => {
    expect(loopProblem(80, 101, 225)).toBeNull();
    expect(loopProblem(80, 81, 225)).toMatch(/at least 2 seconds/);
    expect(loopProblem(200, 230, 225)).toBe('The loop ends after the track');
    expect(loopProblem(null, 90, 225)).toBe('Set both ends of the loop');
  });
});

describe('full-pane skips and loop', () => {
  it('skips ten seconds either way, only when the track has a clock', () => {
    const controls = controlMocks();
    const { rerender } = render(
      <SonosPlayback status={status()} progress={progress} controls={controls} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Back 10 seconds' }));
    fireEvent.click(screen.getByRole('button', { name: 'Forward 10 seconds' }));
    expect(controls.seek).toHaveBeenNthCalledWith(1, -10);
    expect(controls.seek).toHaveBeenNthCalledWith(2, 10);

    rerender(<SonosPlayback status={status()} progress={null} controls={controls} />);
    expect(screen.getByRole('button', { name: 'Forward 10 seconds' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Loop' })).toBeDisabled();
  });

  it('scrubs to where the drag is released and shows that time until the seek lands', async () => {
    const seek = deferred<{ message: string }>();
    const controls = controlMocks({ seekTo: vi.fn().mockReturnValue(seek.promise) });
    render(<SonosPlayback status={status()} progress={progress} controls={controls} />);
    const slider = screen.getByRole('slider', { name: 'Track position' });
    expect(slider).toHaveValue('83');
    expect(slider).toHaveAttribute('aria-valuetext', '1:23 of 3:45');

    fireEvent.change(slider, { target: { value: '150' } });
    expect(controls.seekTo).toHaveBeenCalledWith(150);
    expect(slider).toHaveValue('150');
    expect(screen.getByText('2:30')).toBeInTheDocument();

    await act(async () => {
      seek.resolve({ message: 'Sonos at 2:30' });
      await seek.promise;
    });
    expect(screen.queryByText('2:30')).not.toBeInTheDocument();
    expect(slider).toHaveValue('83');
  });

  it('starts collapsed and loops the range marked with Now and the nudges', () => {
    const controls = controlMocks();
    render(<SonosPlayback status={status()} progress={progress} controls={controls} />);
    const toggle = screen.getByRole('button', { name: 'Loop' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('button', { name: 'Start loop' })).not.toBeInTheDocument();

    fireEvent.click(toggle);
    const start = screen.getByRole('button', { name: 'Start loop' });
    expect(start).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Set From to the current position' }));
    expect(screen.getByRole('textbox', { name: 'From' })).toHaveValue('1:23');
    fireEvent.change(screen.getByRole('textbox', { name: 'To' }), { target: { value: '1:40' } });
    fireEvent.blur(screen.getByRole('textbox', { name: 'To' }));
    fireEvent.click(screen.getByRole('button', { name: 'To: 1 second later' }));
    expect(screen.getByRole('textbox', { name: 'To' })).toHaveValue('1:41');

    fireEvent.click(start);
    expect(controls.startLoop).toHaveBeenCalledWith(83, 101);
  });

  it('flags a too-short range and reverts unreadable typing', () => {
    render(<SonosPlayback status={status()} progress={progress} controls={controlMocks()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Loop' }));
    const from = screen.getByRole('textbox', { name: 'From' });
    fireEvent.change(from, { target: { value: 'soon' } });
    fireEvent.blur(from);
    expect(from).toHaveValue('');

    fireEvent.change(from, { target: { value: '90' } });
    fireEvent.keyDown(from, { key: 'Enter' });
    fireEvent.click(screen.getByRole('button', { name: 'Set To to the current position' }));
    expect(screen.getByText(/at least 2 seconds/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start loop' })).toBeDisabled();
  });

  it('shows a running loop, updates it only after an edit, and stops it', () => {
    const controls = controlMocks();
    const running = status({ active: true, startSeconds: 80, endSeconds: 101 });
    render(<SonosPlayback status={running} progress={progress} controls={controls} />);
    expect(screen.getByText(/1:20–1:41/)).toHaveTextContent('Loop 1:20–1:41');

    fireEvent.click(screen.getByRole('button', { name: 'Loop (running)' }));
    const update = screen.getByRole('button', { name: 'Update loop' });
    expect(update).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'From: 1 second earlier' }));
    fireEvent.click(update);
    expect(controls.startLoop).toHaveBeenCalledWith(79, 101);
    fireEvent.click(screen.getByRole('button', { name: 'Stop loop' }));
    expect(controls.stopLoop).toHaveBeenCalledOnce();
  });

  it('says why the Pi ended a loop', () => {
    const ended = status({ active: false, notice: 'Loop stopped: the track changed' });
    render(<SonosPlayback status={ended} progress={progress} controls={controlMocks()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Loop' }));
    expect(screen.getByText('Loop stopped: the track changed')).toBeInTheDocument();
  });
});
