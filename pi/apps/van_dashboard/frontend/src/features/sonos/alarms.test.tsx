import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiRequestError, getJson, postForm } from '../../api/client';
import { useSonosAlarmControls } from './alarmControls';
import { fetchAlarms } from './alarms';
import { SonosAlarms } from './SonosAlarms';

vi.mock('../../api/client', async (original) => ({
  ...(await original<typeof import('../../api/client')>()),
  getJson: vi.fn(),
  postForm: vi.fn(),
}));

function payload() {
  return {
    ok: true,
    alarms: [
      {
        id: '10',
        revision: 'rev1',
        room: 'front',
        room_name: 'Front',
        start_time: '07:45:00',
        duration: '',
        recurrence: 'ON_15',
        enabled: false,
        volume: 63,
        include_linked_zones: true,
        play_mode: 'SHUFFLE',
        sound_title: 'Morning mix',
      },
    ],
    rooms: [{ id: 'front', name: 'Front' }],
    local_time: '2026-10-15 19:10:00',
    play_modes: ['NORMAL', 'SHUFFLE'],
  };
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getJson).mockImplementation(async (url) =>
    url.includes('alarm-favorites')
      ? { ok: true, favorites: [{ id: 'FV:2/7', title: 'Radio' }] }
      : payload(),
  );
  vi.mocked(postForm).mockResolvedValue({ ok: true, id: '10', message: 'Alarm saved' });
});
afterEach(cleanup);

function AlarmHarness() {
  const controls = useSonosAlarmControls(true);
  return <SonosAlarms controls={controls} />;
}

async function openAlarms() {
  render(<AlarmHarness />);
  fireEvent.click(screen.getByRole('button', { name: 'Alarms' }));
  await screen.findByText('07:45 · Front');
  await waitFor(() => expect(screen.getByRole('button', { name: 'Add alarm' })).toBeEnabled());
}

describe('Sonos alarms', () => {
  it('loads only when opened and displays repeat days and speaker clock', async () => {
    render(<AlarmHarness />);
    expect(getJson).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Alarms' }));
    await screen.findByText('Mon, Fri · Off');
    expect(screen.getByText(/Speaker clock: 2026-10-15 19:10:00/)).toBeInTheDocument();
    expect(postForm).not.toHaveBeenCalled();
  });

  it('edits without replacing current sound and saves selected days', async () => {
    await openAlarms();
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    expect(screen.getByLabelText('Sound')).toHaveValue('keep');
    fireEvent.change(screen.getByLabelText('Time'), { target: { value: '08:30:00' } });
    fireEvent.click(screen.getByLabelText('Wed'));
    fireEvent.click(screen.getByRole('button', { name: 'Save alarm' }));
    await waitFor(() =>
      expect(postForm).toHaveBeenCalledWith(
        '/api/speakers/alarms/save',
        expect.objectContaining({
          id: '10',
          revision: 'rev1',
          sound: 'keep',
          start_time: '08:30:00',
          recurrence: 'ON_135',
          enabled: '0',
          include_linked_zones: '1',
          play_mode: 'SHUFFLE',
          duration: '',
        }),
      ),
    );
  });

  it('creates a disabled chime alarm by default and allows choosing a favorite', async () => {
    await openAlarms();
    fireEvent.click(screen.getByRole('button', { name: 'Add alarm' }));
    expect(screen.getByLabelText('Sound')).toHaveValue('chime');
    expect(screen.getByRole('checkbox', { name: 'Enabled' })).not.toBeChecked();
    fireEvent.change(screen.getByLabelText('Sound'), { target: { value: 'favorite:FV:2/7' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save alarm' }));
    await waitFor(() =>
      expect(postForm).toHaveBeenCalledWith(
        '/api/speakers/alarms/save',
        expect.objectContaining({
          id: '',
          revision: '',
          sound: 'favorite:FV:2/7',
          enabled: '0',
          volume: 20,
        }),
      ),
    );
  });

  it('requires confirmation before deleting and sends the current revision', async () => {
    await openAlarms();
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    expect(postForm).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm delete' }));
    await waitFor(() =>
      expect(postForm).toHaveBeenCalledWith('/api/speakers/alarms/delete', {
        id: '10',
        revision: 'rev1',
      }),
    );
  });

  it('serializes changes and refreshes after an uncertain failure without retrying', async () => {
    let rejectRequest: (error: Error) => void = () => {};
    vi.mocked(postForm).mockImplementation(
      () =>
        new Promise((_resolve, reject) => {
          rejectRequest = reject;
        }),
    );
    await openAlarms();
    const enable = screen.getByRole('button', { name: /Enable alarm/ });
    fireEvent.click(enable);
    fireEvent.click(enable);
    expect(postForm).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: 'Add alarm' })).toBeDisabled();
    const reads = vi.mocked(getJson).mock.calls.filter(([url]) => url.endsWith('/alarms')).length;
    await act(async () => rejectRequest(new Error('Sonos did not confirm the change')));
    expect(await screen.findByRole('alert')).toHaveTextContent('Sonos did not confirm');
    await waitFor(() =>
      expect(
        vi.mocked(getJson).mock.calls.filter(([url]) => url.endsWith('/alarms')).length,
      ).toBeGreaterThan(reads),
    );
    expect(postForm).toHaveBeenCalledTimes(1);
  });

  it('keeps alarms editable when favorites are unavailable', async () => {
    vi.mocked(getJson).mockImplementation(async (url) => {
      if (url.includes('alarm-favorites')) throw new Error('Favorites unavailable');
      return payload();
    });
    await openAlarms();
    await screen.findByText('Favorites unavailable');
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    expect(
      within(screen.getByLabelText('Sound')).getByText('Keep current · Morning mix'),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save alarm' })).toBeEnabled();
  });

  it('blocks empty custom day selections', async () => {
    await openAlarms();
    fireEvent.click(screen.getByRole('button', { name: 'Add alarm' }));
    fireEvent.change(screen.getByLabelText('Repeat'), { target: { value: 'custom' } });
    expect(screen.getByRole('button', { name: 'Save alarm' })).toBeDisabled();
    fireEvent.click(screen.getByLabelText('Sun'));
    expect(screen.getByRole('button', { name: 'Save alarm' })).toBeEnabled();
  });

  it('preserves a rejected draft so validation can be corrected', async () => {
    vi.mocked(postForm).mockRejectedValueOnce(
      new ApiRequestError('Duration is not a valid time', 400, null),
    );
    await openAlarms();
    fireEvent.click(screen.getByRole('button', { name: 'Add alarm' }));
    fireEvent.change(screen.getByLabelText('Sound'), { target: { value: 'favorite:FV:2/7' } });
    fireEvent.change(screen.getByLabelText(/Duration/), { target: { value: '99:99' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save alarm' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Duration is not a valid time');
    expect(screen.getByLabelText(/Duration/)).toHaveValue('99:99');
    expect(screen.getByLabelText('Sound')).toHaveValue('favorite:FV:2/7');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save alarm' })).toBeEnabled());
    fireEvent.change(screen.getByLabelText(/Duration/), { target: { value: '01:00' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save alarm' }));
    await waitFor(() => expect(postForm).toHaveBeenCalledTimes(2));
    expect(vi.mocked(postForm).mock.calls[1]?.[1]).toMatchObject({
      duration: '01:00',
      sound: 'favorite:FV:2/7',
    });
  });

  it('displays the speaker timezone beside the time picker', async () => {
    vi.mocked(getJson).mockImplementation(async (url) =>
      url.includes('alarm-favorites')
        ? { ok: true, favorites: [] }
        : { ...payload(), time_zone: 'MDT' },
    );
    await openAlarms();
    fireEvent.click(screen.getByRole('button', { name: 'Add alarm' }));
    expect(screen.getByLabelText('Time (MDT)')).toHaveValue('07:00:00');
    expect(screen.getByText('MDT')).toBeInTheDocument();
  });

  it('filters the list by weekday and enabled state without device writes', async () => {
    await openAlarms();
    fireEvent.change(screen.getByLabelText('Includes day of week'), { target: { value: '0' } });
    expect(screen.getByText('No alarms match these filters.')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Includes day of week'), { target: { value: '5' } });
    expect(screen.getByText('07:45 · Front')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox', { name: 'Enabled' }), {
      target: { value: 'true' },
    });
    expect(screen.getByText('No alarms match these filters.')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox', { name: 'Enabled' }), {
      target: { value: 'false' },
    });
    expect(screen.getByText('07:45 · Front')).toBeInTheDocument();
    expect(postForm).not.toHaveBeenCalled();
  });

  it('rejects invalid speaker responses', async () => {
    const invalid = payload();
    invalid.alarms = invalid.alarms.map((alarm) => ({ ...alarm, volume: 101 }));
    vi.mocked(getJson).mockResolvedValue(invalid);
    await expect(fetchAlarms(new AbortController().signal)).rejects.toThrow(TypeError);
  });
});
