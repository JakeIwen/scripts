import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { decode } from '../../api/schema';
import { deferred } from '../../test/deferred';
import { ToastProvider } from '../../components/ToastProvider';
import { MediaOrganizerFeature } from '../mediaOrganizer/MediaOrganizerFeature';
import { sampleStatus as organizerStatus } from '../mediaOrganizer/testFixtures';
import { IptWatchSheet } from './IptWatchSheet';
import { useIptWatch, type IptWatch } from './useIptWatch';
import { CandidateState, Login, statusSchema } from './schema';
import { sampleStatus } from './testFixtures';

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute('open', '');
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute('open');
  };
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.location.hash = '';
});
function probeWatch(status = sampleStatus()): IptWatch {
  return {
    resource: {
      data: status,
      error: null,
      initialLoading: false,
      refreshing: false,
      lastUpdatedAt: Date.now(),
      refresh: vi.fn(),
    },
    error: null,
    blocked: false,
    checking: false,
    pending: null,
    clearError: vi.fn(),
    check: vi.fn(async () => true),
    wantlist: vi.fn(async () => true),
    candidate: vi.fn(async () => true),
  };
}
function Probe() {
  const watch = useIptWatch(true);
  return <IptWatchSheet open onClose={() => {}} watch={watch} />;
}
function mockApi() {
  const status = sampleStatus();
  const calls: { path: string; payload: Record<string, unknown> }[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, init?: RequestInit) => {
      const payload = init?.body ? JSON.parse(String(init.body)) : {};
      calls.push({ path, payload });
      let result: unknown = status;
      if (path === '/api/media-organizer/status') result = organizerStatus();
      if (path === '/api/media-organizer/plans/plan-1') result = {};
      if (path === '/api/ipt-watch/wantlist') result = { wanted: status.wanted };
      if (path === '/api/ipt-watch/check') {
        status.job = {
          stage: 'check',
          started_at: '2026-10-29T08:00:00Z',
          finished_at: null,
          ok: null,
          error: null,
          result_id: null,
        };
        result = status.job;
      }
      if (path === '/api/ipt-watch/download' || path === '/api/ipt-watch/dismiss') {
        const candidate = status.candidates.find((c) => c.torrent_id === payload.torrent_id)!;
        candidate.status = path.endsWith('/download')
          ? CandidateState.Downloaded
          : CandidateState.Dismissed;
        result = candidate;
      }
      return new Response(JSON.stringify(result));
    }),
  );
  return { calls, status };
}

describe('watchlist wire contract', () => {
  it('decodes the complete CLI status and rejects unknown vocabularies and bad fields', () => {
    expect(decode(statusSchema, sampleStatus(), 'watchlist')).toEqual(sampleStatus());
    for (const bad of [
      { login: 'maybe' },
      { new_candidate_count: '3' },
      { wanted: [{ ...sampleStatus().wanted[0], source: 'other' }] },
      { candidates: [{ ...sampleStatus().candidates[0], status: 'waiting' }] },
      { candidates: [{ ...sampleStatus().candidates[0], episode: -1 }] },
    ])
      expect(() => decode(statusSchema, { ...sampleStatus(), ...bad }, 'watchlist')).toThrow(
        TypeError,
      );
  });
});
describe('watchlist sheet', () => {
  it.each([
    [Login.Ok, 'Login ok'],
    [Login.Needed, 'Login needed'],
    [Login.Unknown, 'Login unknown'],
  ])('renders login %s and the check header', (login, label) => {
    const watch = probeWatch(
      sampleStatus({ login, last_error: login === Login.Unknown ? 'site unreachable' : null }),
    );
    render(<IptWatchSheet open onClose={() => {}} watch={watch} />);
    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'New releases (3)' })).toBeInTheDocument();
    expect(screen.getByText(/Last check/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Check now' }));
    expect(watch.check).toHaveBeenCalledOnce();
  });
  it('groups new candidates, labels packs and hides handled rows until expanded', () => {
    render(<IptWatchSheet open onClose={() => {}} watch={probeWatch()} />);
    const silo = screen.getByRole('region', { name: 'Silo candidates' });
    expect(within(silo).getByText('Silo.S03E08.1080p.x264')).toBeInTheDocument();
    expect(within(silo).getByText('Season pack')).toBeInTheDocument();
    expect(within(silo).getAllByText('Free-leech')).toHaveLength(2);
    expect(within(silo).getAllByText(/21 seeders \/ 2 leechers/)[0]).toHaveTextContent(
      '3.1 minutes ago',
    );
    expect(screen.getByRole('region', { name: 'Severance candidates' })).toBeInTheDocument();
    expect(screen.queryByText('Silo.handled')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Show handled (1)' }));
    expect(screen.getByText('Silo.handled')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Download Silo.handled' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Hide handled (1)' }));
    expect(screen.queryByText('Silo.handled')).not.toBeInTheDocument();
  });
  it('shows disk and source details, and removes only manual shows', () => {
    const watch = probeWatch();
    render(<IptWatchSheet open onClose={() => {}} watch={watch} />);
    fireEvent.click(screen.getByRole('button', { name: 'Wanted shows (2)' }));
    expect(screen.getByText(/On disk: S03E07/)).toBeInTheDocument();
    expect(screen.getByText('Library')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Remove Silo' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Pause Silo' }));
    fireEvent.click(screen.getByRole('button', { name: 'Resume Severance' }));
    fireEvent.click(screen.getByRole('button', { name: 'Remove Severance' }));
    expect(watch.wantlist).toHaveBeenNthCalledWith(1, { action: 'pause', name: 'Silo' });
    expect(watch.wantlist).toHaveBeenNthCalledWith(2, { action: 'resume', name: 'Severance' });
    expect(watch.wantlist).toHaveBeenNthCalledWith(3, { action: 'remove', name: 'Severance' });
  });
  it('adds a show with optional season/episode through the API and resets the form', async () => {
    const { calls } = mockApi();
    render(
      <ToastProvider>
        <Probe />
      </ToastProvider>,
    );
    await screen.findByText('Login ok');
    fireEvent.click(screen.getByRole('button', { name: 'Wanted shows (2)' }));
    fireEvent.change(screen.getByLabelText('Show name'), { target: { value: ' The Expanse ' } });
    fireEvent.change(screen.getByLabelText('Season (optional)'), { target: { value: '3' } });
    fireEvent.change(screen.getByLabelText('Episode (optional)'), { target: { value: '7' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add show' }));
    await waitFor(() =>
      expect(calls).toContainEqual({
        path: '/api/ipt-watch/wantlist',
        payload: { action: 'add', name: 'The Expanse', season: 3, episode: 7 },
      }),
    );
    await waitFor(() => expect(screen.getByLabelText('Show name')).toHaveValue(''));
  });
  it('confirms packs once and downloads ordinary episodes without confirmation', async () => {
    const { calls } = mockApi();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(
      <ToastProvider>
        <Probe />
      </ToastProvider>,
    );
    await screen.findByText('Login ok');
    fireEvent.click(screen.getByRole('button', { name: 'Download Silo.S03.1080p.x264' }));
    expect(confirm).toHaveBeenCalledOnce();
    expect(calls.some((c) => c.path.endsWith('/download'))).toBe(false);
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole('button', { name: 'Download Silo.S03.1080p.x264' }));
    await waitFor(() =>
      expect(calls).toContainEqual({
        path: '/api/ipt-watch/download',
        payload: { torrent_id: '124' },
      }),
    );
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Download Silo.S03E08.1080p.x264' })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Download Silo.S03E08.1080p.x264' }));
    await waitFor(() =>
      expect(calls).toContainEqual({
        path: '/api/ipt-watch/download',
        payload: { torrent_id: '123' },
      }),
    );
    expect(confirm).toHaveBeenCalledTimes(2);
  });
  it('blocks controls while a check runs and download when the watch folder is unavailable', async () => {
    const { calls, status } = mockApi();
    status.tfiles_writable = false;
    render(
      <ToastProvider>
        <Probe />
      </ToastProvider>,
    );
    await screen.findByText('Login ok');
    expect(screen.getByRole('button', { name: 'Download Silo.S03E08.1080p.x264' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss Silo.S03E08.1080p.x264' }));
    await waitFor(() =>
      expect(calls).toContainEqual({
        path: '/api/ipt-watch/dismiss',
        payload: { torrent_id: '123' },
      }),
    );
    await waitFor(() => expect(screen.getByRole('button', { name: 'Check now' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Check now' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Checking…' })).toBeDisabled());
    fireEvent.click(screen.getByRole('button', { name: 'Wanted shows (2)' }));
    expect(screen.getByRole('button', { name: 'Pause Silo' })).toBeDisabled();
  });
  it('wires the second Movies & TV button and candidate summary', async () => {
    mockApi();
    render(
      <ToastProvider>
        <MediaOrganizerFeature />
      </ToastProvider>,
    );
    await screen.findByText(/3 new releases/);
    expect(screen.getByRole('button', { name: 'Media organizer' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Watchlist' }));
    expect(window.location.hash).toBe('#ipt-watch');
    expect(await screen.findByRole('dialog', { name: 'Watchlist' })).toBeInTheDocument();
  });
});

describe('watchlist guidance and operation feedback', () => {
  it('rejects an episode without a season before making a request', async () => {
    const { calls } = mockApi();
    render(
      <ToastProvider>
        <Probe />
      </ToastProvider>,
    );
    await screen.findByText('Login ok');
    fireEvent.click(screen.getByRole('button', { name: 'Wanted shows (2)' }));
    fireEvent.change(screen.getByLabelText('Show name'), { target: { value: 'Silo' } });
    fireEvent.change(screen.getByLabelText('Episode (optional)'), { target: { value: '4' } });
    expect(screen.getByRole('alert')).toHaveTextContent('Enter a season');
    expect(screen.getByRole('button', { name: 'Add show' })).toBeDisabled();
    expect(calls.some((c) => c.path.endsWith('/wantlist'))).toBe(false);
    fireEvent.focus(screen.getByRole('button', { name: 'About Already on disk' }));
    expect(screen.getByRole('tooltip')).toHaveTextContent('already have, not what you want');
  });
  it('shows request-specific download feedback until the request completes, then shows its result', async () => {
    const { calls } = mockApi();
    const original = globalThis.fetch;
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    vi.stubGlobal(
      'fetch',
      vi.fn(async (path: string, init?: RequestInit) => {
        if (path.endsWith('/download')) await gate;
        return original(path, init);
      }),
    );
    render(
      <ToastProvider>
        <Probe />
      </ToastProvider>,
    );
    await screen.findByText('Login ok');
    fireEvent.click(screen.getByRole('button', { name: 'Download Silo.S03E08.1080p.x264' }));
    expect(await screen.findByText('Sending torrent…')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Check now' })).toBeDisabled();
    release();
    expect(await screen.findByText(/Torrent sent to qBittorrent/)).toBeInTheDocument();
    await waitFor(() =>
      expect(
        screen.queryByRole('button', { name: 'Download Silo.S03E08.1080p.x264' }),
      ).not.toBeInTheDocument(),
    );
    expect(calls.filter((c) => c.path.endsWith('/download'))).toHaveLength(1);
  });
  it('shows indeterminate check progress and a finished result, without an invented percentage', async () => {
    const { status } = mockApi();
    render(
      <ToastProvider>
        <Probe />
      </ToastProvider>,
    );
    await screen.findByText('Login ok');
    fireEvent.click(screen.getByRole('button', { name: 'Check now' }));
    const bar = await screen.findByRole('progressbar', { name: 'Watchlist check progress' });
    expect(bar).not.toHaveAttribute('value');
    status.job = { ...status.job!, finished_at: '2026-10-29T07:00:00Z', ok: true };
    expect(
      await screen.findByText('Check complete · 3 new releases available.', undefined, {
        timeout: 4000,
      }),
    ).toBeInTheDocument();
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  });
  it('keeps request errors actionable and retries status without repeating mutations', async () => {
    mockApi();
    const original = globalThis.fetch;
    const download = vi.fn();
    vi.stubGlobal(
      'fetch',
      vi.fn(async (path: string, init?: RequestInit) => {
        if (path.endsWith('/download')) {
          download();
          return new Response(JSON.stringify({ message: 'Site unavailable' }), { status: 503 });
        }
        return original(path, init);
      }),
    );
    render(
      <ToastProvider>
        <Probe />
      </ToastProvider>,
    );
    await screen.findByText('Login ok');
    fireEvent.click(screen.getByRole('button', { name: 'Download Silo.S03E08.1080p.x264' }));
    await waitFor(() =>
      expect(screen.getAllByRole('alert')[0]).toHaveTextContent(
        'Refresh status and check the result before retrying',
      ),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Refresh status' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Check now' })).toBeEnabled());
    expect(download).toHaveBeenCalledOnce();
  });
});

it('releases the local check guard when a later check has already completed elsewhere', async () => {
  const { status } = mockApi();
  render(
    <ToastProvider>
      <Probe />
    </ToastProvider>,
  );
  await screen.findByText('Login ok');
  fireEvent.click(screen.getByRole('button', { name: 'Check now' }));
  await screen.findByRole('progressbar', { name: 'Watchlist check progress' });
  status.job = {
    ...status.job!,
    started_at: '2026-10-31T08:00:00Z',
    finished_at: '2026-10-31T08:01:00Z',
    ok: true,
  };
  await waitFor(() => expect(screen.getByRole('button', { name: 'Check now' })).toBeEnabled(), {
    timeout: 4000,
  });
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
});

describe('watchlist review follow-up regressions', () => {
  it('releases an accepted check lost by a backend restart without replaying it', async () => {
    const { status, calls } = mockApi();
    const original = globalThis.fetch;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (path: string, init?: RequestInit) => {
        const response = await original(path, init);
        if (path.endsWith('/check')) status.job = null;
        return response;
      }),
    );
    render(
      <ToastProvider>
        <MediaOrganizerFeature />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Watchlist' }));
    await screen.findByText('Login ok');
    fireEvent.click(screen.getByRole('button', { name: 'Check now' }));
    expect(await screen.findByText(/Check outcome unknown:/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Check now' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Close Watchlist' }));
    fireEvent.click(screen.getByRole('button', { name: 'Watchlist' }));
    expect(screen.getByRole('button', { name: 'Check now' })).toBeEnabled();
    expect(calls.filter((call) => call.path.endsWith('/check'))).toHaveLength(1);
  });
  it.each(['download', 'dismiss'])(
    'holds %s until a new status read, not the overlapping older poll, finishes',
    async (operation) => {
      const { status, calls } = mockApi();
      const original = globalThis.fetch;
      const oldRead = deferred<Response>();
      const newRead = deferred<Response>();
      const stale = JSON.stringify(status);
      let holdNext = false;
      let oldStarted = false;
      let changed = false;
      let newStarted = false;
      vi.stubGlobal(
        'fetch',
        vi.fn(async (path: string, init?: RequestInit) => {
          if (path === '/api/ipt-watch/status' && holdNext) {
            holdNext = false;
            oldStarted = true;
            return oldRead.promise;
          }
          if (path === '/api/ipt-watch/status' && changed) {
            newStarted = true;
            return newRead.promise;
          }
          const response = await original(path, init);
          if (path.endsWith('/' + operation)) changed = true;
          return response;
        }),
      );
      render(
        <ToastProvider>
          <Probe />
        </ToastProvider>,
      );
      await screen.findByText('Login ok');
      holdNext = true;
      fireEvent(window, new Event('focus'));
      await waitFor(() => expect(oldStarted).toBe(true));
      fireEvent.click(
        screen.getByRole('button', {
          name: `${operation === 'download' ? 'Download' : 'Dismiss'} Silo.S03E08.1080p.x264`,
        }),
      );
      await waitFor(() => expect(changed).toBe(true));
      await act(async () => oldRead.resolve(new Response(stale)));
      await waitFor(() => expect(newStarted).toBe(true));
      expect(
        screen.getByRole('button', { name: 'Download Silo.S03E08.1080p.x264' }),
      ).toBeDisabled();
      expect(screen.getByRole('button', { name: 'Dismiss Silo.S03E08.1080p.x264' })).toBeDisabled();
      await act(async () => newRead.resolve(new Response(JSON.stringify(status))));
      await waitFor(() =>
        expect(
          screen.queryByRole('button', { name: 'Download Silo.S03E08.1080p.x264' }),
        ).not.toBeInTheDocument(),
      );
      expect(calls.filter((call) => call.path.endsWith('/' + operation))).toHaveLength(1);
      expect(status.candidates[0]?.status).toBe(
        operation === 'download' ? CandidateState.Downloaded : CandidateState.Dismissed,
      );
    },
  );
});

it('does not treat a pre-acceptance idle poll as a missing accepted job', async () => {
  const { status, calls } = mockApi();
  const original = globalThis.fetch;
  const oldRead = deferred<Response>();
  const newRead = deferred<Response>();
  const stale = JSON.stringify(status);
  let holdNext = false;
  let oldStarted = false;
  let accepted = false;
  let newStarted = false;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, init?: RequestInit) => {
      if (path === '/api/ipt-watch/status' && holdNext) {
        holdNext = false;
        oldStarted = true;
        return oldRead.promise;
      }
      if (path === '/api/ipt-watch/status' && accepted) {
        newStarted = true;
        return newRead.promise;
      }
      const response = await original(path, init);
      if (path.endsWith('/check')) accepted = true;
      return response;
    }),
  );
  render(
    <ToastProvider>
      <Probe />
    </ToastProvider>,
  );
  await screen.findByText('Login ok');
  holdNext = true;
  fireEvent(window, new Event('focus'));
  await waitFor(() => expect(oldStarted).toBe(true));
  fireEvent.click(screen.getByRole('button', { name: 'Check now' }));
  await waitFor(() => expect(accepted).toBe(true));
  await act(async () => oldRead.resolve(new Response(stale)));
  await waitFor(() => expect(newStarted).toBe(true));
  expect(screen.getByRole('button', { name: 'Checking…' })).toBeDisabled();
  expect(screen.queryByText(/Check outcome unknown:/)).not.toBeInTheDocument();
  await act(async () => newRead.resolve(new Response(JSON.stringify(status))));
  expect(screen.getByRole('button', { name: 'Checking…' })).toBeDisabled();
  expect(screen.queryByText(/Check outcome unknown:/)).not.toBeInTheDocument();
  expect(calls.filter((call) => call.path.endsWith('/check'))).toHaveLength(1);
});
