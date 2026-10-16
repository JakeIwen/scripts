import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, expect, it, vi } from 'vitest';

import { getJson, postForm } from '../../api/client';
import { SonosFeature } from './SonosFeature';

vi.mock('../../api/client', async (original) => ({
  ...(await original<typeof import('../../api/client')>()),
  getJson: vi.fn(),
  postForm: vi.fn(),
}));
vi.mock('../../components/ToastProvider', () => ({ useToast: () => ({ showToast: vi.fn() }) }));
vi.mock('./hooks', () => ({
  useSonosStatus: () => ({
    data: null,
    error: null,
    refreshing: false,
    lastUpdatedAt: null,
    initialLoading: true,
    refresh: vi.fn(),
  }),
}));

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute('open', '');
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute('open');
  };
});
beforeEach(() => vi.resetAllMocks());
afterEach(cleanup);

it.each(['confirmed', 'uncertain'])(
  'keeps %s writes single-flight through collapsing and closing views',
  async (outcome) => {
    let finish: () => void = () => {};
    let completed = false;
    const saved = {
      id: '11',
      revision: 'new',
      room: 'front',
      room_name: 'Front',
      start_time: '07:00:00',
      duration: '01:00:00',
      recurrence: 'DAILY',
      enabled: false,
      volume: 20,
      include_linked_zones: false,
      play_mode: 'NORMAL',
      sound_title: 'Sonos chime',
    };
    vi.mocked(getJson).mockImplementation(async (url) =>
      url.includes('alarm-favorites')
        ? { ok: true, favorites: [] }
        : {
            ok: true,
            alarms: completed ? [saved] : [],
            rooms: [{ id: 'front', name: 'Front' }],
            local_time: null,
            play_modes: ['NORMAL'],
          },
    );
    vi.mocked(postForm).mockImplementation(
      () =>
        new Promise((resolve, reject) => {
          finish = () => {
            completed = true;
            if (outcome === 'confirmed') resolve({ ok: true, id: '11', message: 'Alarm saved' });
            else reject(new Error('Sonos did not confirm the change'));
          };
        }),
    );

    render(<SonosFeature />);
    fireEvent.click(screen.getByRole('button', { name: 'Open Sonos details' }));
    fireEvent.click(screen.getByRole('button', { name: 'Alarms' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Add alarm' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Add alarm' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save alarm' }));
    expect(postForm).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: 'Alarms' }));
    fireEvent.click(screen.getByRole('button', { name: 'Alarms' }));
    expect(screen.getByRole('button', { name: 'Add alarm' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Close Sonos' }));
    fireEvent.click(screen.getByRole('button', { name: 'Open Sonos details' }));
    expect(screen.getByRole('button', { name: 'Add alarm' })).toBeDisabled();
    const saving = screen.getByRole('button', { name: 'Saving…' });
    expect(saving).toBeDisabled();
    fireEvent.click(saving);
    expect(postForm).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: 'Close Sonos' }));
    const reads = vi.mocked(getJson).mock.calls.length;
    await act(async () => finish());
    await waitFor(() => expect(vi.mocked(getJson).mock.calls.length).toBeGreaterThan(reads));
    fireEvent.click(screen.getByRole('button', { name: 'Open Sonos details' }));
    expect(await screen.findByText('07:00 · Front')).toBeInTheDocument();
    expect(
      screen.getByText(
        outcome === 'confirmed' ? 'Alarm saved' : 'Sonos did not confirm the change',
      ),
    ).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Add alarm' })).toBeEnabled());
    expect(postForm).toHaveBeenCalledTimes(1);
  },
);
