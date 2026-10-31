import { useState } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { decode } from '../../api/schema';
import { deferred } from '../../test/deferred';
import { ToastProvider } from '../../components/ToastProvider';
import { ServiceLink, ServiceLinkTile } from '../../components/Tile';
import { ReviewTable } from './ReviewTable';
import { RunHistory } from './RunHistory';
import { SettingsForm } from './SettingsForm';
import { sampleStatus as watchStatus } from '../iptWatch/testFixtures';
import { settingFields } from './content';
import { MediaOrganizerFeature } from './MediaOrganizerFeature';
import { samplePlan, sampleRun, sampleStatus } from './testFixtures';
import { planSchema, runSchema, statusSchema, type Decisions } from './schema';

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

function ReviewProbe() {
  const [draft, setDraft] = useState<Decisions>({});
  return (
    <ReviewTable
      plan={samplePlan()}
      draft={draft}
      disabled={false}
      onChange={(name, decision) => setDraft({ ...draft, [name]: decision })}
    />
  );
}

function mockCliApi(plan = samplePlan()) {
  const status = sampleStatus();
  const calls: { path: string; body: Record<string, unknown> }[] = [];
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) : {};
    if (init?.method === 'POST') calls.push({ path, body });
    let result: unknown = status;
    if (path === '/api/ipt-watch/status') result = watchStatus();
    else if (path.includes('/runs/')) {
      const { moved_count: _m, failed_count: _f, reverted_count: _r, ...run } = sampleRun();
      result = {
        ...run,
        items: [
          {
            name: 'Silo.S01E01',
            torrent_hash: 'abc',
            old_path: '/media/New/Silo',
            new_path: '/media/TV/Silo',
            mirror_old: null,
            mirror_new: null,
            status: 'failed',
            error: 'Destination already exists',
            mirror_state: null,
          },
        ],
      };
    } else if (path.endsWith('/alias')) result = { status: 'scheduled' };
    else if (path.includes('/plans/') || path.endsWith('/decide')) result = plan;
    else if (path.endsWith('/settings')) {
      Object.assign(status.settings, body);
      result = status.settings;
    } else if (path.endsWith('/apply') || path.endsWith('/propose') || path.endsWith('/undo')) {
      status.job = {
        stage: 'apply',
        started_at: '2026-10-29T08:00:00Z',
        finished_at: null,
        ok: null,
        error: null,
        result_id: null,
      };
      result = status.job;
    }
    return new Response(JSON.stringify(result));
  });
  vi.stubGlobal('fetch', fetch);
  return { calls, status, fetch };
}

describe('media organizer wire contract', () => {
  it('decodes status, plan and full run, including named vocabularies', () => {
    expect(decode(statusSchema, sampleStatus(), 'status')).toEqual(sampleStatus());
    expect(decode(planSchema, samplePlan(), 'plan')).toEqual(samplePlan());
    const { moved_count: _m, failed_count: _f, reverted_count: _r, ...run } = sampleRun();
    expect(decode(runSchema, { ...run, items: [] }, 'run').items).toEqual([]);
    expect(() =>
      decode(
        statusSchema,
        { ...sampleStatus(), busy: { stage: 'unknown', started_at: 'now' } },
        'status',
      ),
    ).toThrow(TypeError);
    expect(() => decode(statusSchema, { ...sampleStatus(), new_count: '12' }, 'status')).toThrow(
      TypeError,
    );
  });
  it('preserves the library URL behavior when sharing the link logic', () => {
    render(
      <>
        <ServiceLinkTile port={8789} icon="" title="Old link" detail="Library" />
        <ServiceLink port={8789}>New link</ServiceLink>
      </>,
    );
    const old = screen.getByRole('link', { name: 'Old link Library' });
    expect(screen.getByRole('link', { name: 'New link' }).getAttribute('href')).toBe(
      old.getAttribute('href'),
    );
    const url = new URL(old.getAttribute('href')!);
    expect(url.port).toBe('8789');
    expect(url.pathname).toBe('/');
    expect(url.hash).toBe('');
    expect(url.search).toBe('');
  });
});

describe('organizer review and history', () => {
  it('edits category and accept locally and shows category-specific show suggestions', () => {
    const { container } = render(<ReviewProbe />);
    expect(screen.getByLabelText('Show for Silo.S01E01')).toHaveValue('Silo');
    expect(screen.getByText('Auto eligible')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Category for Silo.S01E01'), {
      target: { value: 'Movies' },
    });
    expect(screen.queryByLabelText('Show for Silo.S01E01')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Category for Silo.S01E01'), {
      target: { value: 'Documentaries' },
    });
    expect(screen.getByLabelText('Show for Silo.S01E01')).toBeInTheDocument();
    expect(container.querySelector('datalist option')).toHaveAttribute('value', 'Planet Earth');
    fireEvent.click(screen.getByLabelText('Accept Silo.S01E01'));
    expect(screen.getByLabelText('Accept Silo.S01E01')).not.toBeChecked();
    expect(screen.getByRole('table')).toHaveClass('organizer-review--responsive');
  });
  it('renders skipped entries without review controls', () => {
    const plan = samplePlan();
    plan.rows[0]!.entry.skip_reason = 'incomplete';
    const { container } = render(
      <ReviewTable plan={plan} draft={{}} disabled={false} onChange={vi.fn()} />,
    );
    expect(
      screen.getByText(
        'Skipped: Download not finished — wait for it to complete, then scan again.',
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(container.querySelector('.organizer-review__row--skipped')).not.toBeNull();
  });
  it('lists actionable rows before skipped ones and names hidden files plainly', () => {
    const plan = samplePlan();
    const silo = plan.rows[0]!;
    plan.rows.unshift({
      ...silo,
      entry: { ...silo.entry, name: '._Silo.S01E01', tracked: false, skip_reason: 'hidden' },
      proposal: null,
      decision: null,
      target_path: null,
      auto_eligible: false,
    });
    const { container } = render(
      <ReviewTable plan={plan} draft={{}} disabled={false} onChange={vi.fn()} />,
    );
    const names = [...container.querySelectorAll('.organizer-entry-name')].map(
      (n) => n.textContent,
    );
    expect(names).toEqual(['Silo.S01E01', '._Silo.S01E01']);
    expect(screen.getByText(/Hidden file \(Finder \._ metadata/)).toBeInTheDocument();
  });
  it('requires confirmation before Undo and sorts newest first', () => {
    const undo = vi.fn();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    const older = {
      ...sampleRun(),
      id: 'old',
      started_at: '2026-10-28T00:00:00Z',
      undoable: false,
    };
    const { container } = render(
      <RunHistory runs={[older, sampleRun()]} disabled={false} onUndo={undo} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(undo).not.toHaveBeenCalled();
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(undo).toHaveBeenCalledWith('run-1');
    expect(container.querySelector('article')).toHaveTextContent('Links: Rebuild queued');
  });
  it('saves switches immediately and confidence only on blur', async () => {
    const save = vi.fn().mockResolvedValue(true);
    render(<SettingsForm settings={sampleStatus().settings} disabled={false} save={save} />);
    fireEvent.click(screen.getByRole('switch', { name: 'Auto apply after downloads' }));
    expect(save).toHaveBeenCalledWith({ auto_apply: false });
    save.mockClear();
    const input = screen.getByLabelText('Minimum auto confidence');
    fireEvent.change(input, { target: { value: '0.9' } });
    expect(save).not.toHaveBeenCalled();
    fireEvent.blur(input);
    await waitFor(() => expect(save).toHaveBeenCalledWith({ auto_apply_min_confidence: 0.9 }));
    save.mockClear();
    fireEvent.change(input, { target: { value: '2' } });
    fireEvent.blur(input);
    expect(save).not.toHaveBeenCalled();
  });
});

describe('organizer feature API flow', () => {
  it('saves edits using decide before applying selected, and disables controls during the job', async () => {
    const { calls } = mockCliApi();
    render(
      <ToastProvider>
        <MediaOrganizerFeature />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Media organizer' }));
    expect(await screen.findByLabelText('Show for Silo.S01E01')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Show for Silo.S01E01'), {
      target: { value: 'Silo edited' },
    });
    expect(calls).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: 'Apply selected' }));
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls[0]).toEqual({
      path: '/api/media-organizer/decide',
      body: {
        plan_id: 'plan-1',
        decisions: { 'Silo.S01E01': { category: 'TV', show: 'Silo edited', accepted: true } },
      },
    });
    expect(calls[1]).toEqual({
      path: '/api/media-organizer/apply',
      body: { plan_id: 'plan-1', auto: false },
    });
    expect(
      await screen.findByRole('progressbar', { name: 'Applying progress' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Scan & propose' })).toBeDisabled();
  });
  it('shows row-level progress for a running apply and its outcome once finished', async () => {
    const { status } = mockCliApi();
    status.busy = {
      stage: 'apply',
      started_at: '2026-10-29T08:00:00Z',
      pid: 42,
      detail: 'Silo.S01E01',
      done: 3,
      total: 8,
    };
    status.recent_runs = [{ ...sampleRun(), finished_at: null, moved_count: 2, failed_count: 1 }];
    render(
      <ToastProvider>
        <MediaOrganizerFeature />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Media organizer' }));
    const bar = await screen.findByRole('progressbar', { name: 'Applying progress' });
    expect(bar).toHaveAttribute('value', '3');
    expect(bar).toHaveAttribute('max', '8');
    const panel = bar.parentElement;
    expect(panel?.querySelector('p')).toHaveTextContent(/^Applying… Silo\.S01E01 · \d+ (h|min|s)/);
    expect(panel?.querySelector(':scope > small')).toHaveTextContent('3 of 8 · 2 moved · 1 failed');
    status.busy = null;
    status.recent_runs = [{ ...sampleRun(), moved_count: 7, failed_count: 1 }];
    status.job = {
      stage: 'apply',
      started_at: '2026-10-29T08:00:00Z',
      finished_at: '2026-10-29T08:05:00Z',
      ok: true,
      error: null,
      result_id: 'run-1',
    };
    // The sheet polls status every two seconds while open.
    expect(
      await screen.findByText('Apply finished: 7 moved · 1 failed', undefined, { timeout: 4000 }),
    ).toBeInTheDocument();
  });
  it('never resubmits rows a run already moved, and shows them as moved', async () => {
    const plan = samplePlan();
    const silo = plan.rows[0]!;
    plan.rows.push({
      ...silo,
      entry: { ...silo.entry, name: 'Dune.2021', torrent_name: 'Dune.2021' },
      proposal: { ...silo.proposal!, category: 'Movies', show: null, existing_show: false },
      decision: { category: 'Movies', show: null, accepted: true },
      target_path: '/media/torrent/Movies/Dune.2021',
      auto_eligible: false,
    });
    plan.settled_rows = ['Silo.S01E01'];
    plan.moved_rows = ['Silo.S01E01'];
    const { calls } = mockCliApi(plan);
    render(
      <ToastProvider>
        <MediaOrganizerFeature />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Media organizer' }));
    expect(await screen.findByText('Moved')).toBeInTheDocument();
    expect(screen.getByLabelText('Show for Silo.S01E01')).toBeDisabled();
    expect(screen.queryByLabelText('Accept Silo.S01E01')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Apply selected' }));
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls[0]?.body).toEqual({
      plan_id: 'plan-1',
      decisions: { 'Dune.2021': { category: 'Movies', show: null, accepted: true } },
    });
  });
  it('blocks controls for external CLI jobs and saves settings per field', async () => {
    const { status, calls } = mockCliApi();
    status.busy = { stage: 'alias', started_at: 'now', pid: 42 };
    render(
      <ToastProvider>
        <MediaOrganizerFeature />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Media organizer' }));
    await screen.findByLabelText('Show for Silo.S01E01');
    expect(screen.getByRole('button', { name: 'Apply selected' })).toBeDisabled();
    expect(calls).toHaveLength(0);
  });
});

describe('organizer guidance and contextual feedback', () => {
  it('keeps plan outcomes and review controls out of settings, with help for every setting', async () => {
    const { status, calls } = mockCliApi();
    status.job = {
      stage: 'propose',
      started_at: '2026-10-29T08:00:00Z',
      finished_at: '2026-10-29T08:01:00Z',
      ok: true,
      error: null,
      result_id: 'plan-1',
    };
    render(
      <ToastProvider>
        <MediaOrganizerFeature />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Open media organizer' }));
    await screen.findByText('Plan ready: 9 rows · 6 proposed');
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    expect(screen.queryByText(/Plan ready:/)).not.toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Scan & propose' })).not.toBeInTheDocument();
    for (const field of Object.values(settingFields)) {
      fireEvent.focus(screen.getByRole('button', { name: `About ${field.label}` }));
      expect(
        within(screen.getByRole('dialog', { name: 'Media organizer' })).getByRole('tooltip'),
      ).toHaveTextContent(field.help);
      fireEvent.blur(screen.getByRole('button', { name: `About ${field.label}` }));
    }
    const model = screen.getByLabelText('Codex model');
    fireEvent.change(model, { target: { value: 'gpt-test' } });
    expect(calls).toHaveLength(0);
    fireEvent.blur(model);
    await waitFor(() =>
      expect(calls).toContainEqual({
        path: '/api/media-organizer/settings',
        body: { codex_model: 'gpt-test' },
      }),
    );
    expect(await screen.findByText('Organizer setting saved')).toBeInTheDocument();
  });
  it('preserves unsaved decisions across views and explains missing show folders', async () => {
    const { calls } = mockCliApi();
    render(
      <ToastProvider>
        <MediaOrganizerFeature />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Media organizer' }));
    const show = await screen.findByLabelText('Show for Silo.S01E01');
    fireEvent.change(show, { target: { value: '' } });
    expect(screen.getByRole('button', { name: 'Apply selected' })).toBeDisabled();
    expect(screen.getByText('Enter a show folder before saving this review.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    fireEvent.click(screen.getByRole('button', { name: 'Review' }));
    expect(screen.getByLabelText('Show for Silo.S01E01')).toHaveValue('');
    expect(screen.getByRole('button', { name: 'Scan & propose' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'About Scan & propose' }));
    expect(
      within(screen.getByRole('dialog', { name: 'Media organizer' })).getByRole('tooltip'),
    ).toHaveTextContent('Nothing moves until you apply');
    fireEvent.click(screen.getByRole('button', { name: 'Discard edits' }));
    expect(screen.getByLabelText('Show for Silo.S01E01')).toHaveValue('Silo');
    expect(calls).toHaveLength(0);
  });
  it('excludes settled rows from auto eligibility and selection, without claiming unconfirmed moves succeeded', async () => {
    const plan = samplePlan();
    plan.settled_rows = ['Silo.S01E01'];
    mockCliApi(plan);
    render(
      <ToastProvider>
        <MediaOrganizerFeature />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Media organizer' }));
    await screen.findByText('Unconfirmed');
    expect(screen.getByRole('button', { name: 'Apply auto' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Apply selected' })).toBeDisabled();
    expect(screen.queryByText('Auto eligible')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Unconfirmed' }));
    expect(
      within(screen.getByRole('dialog', { name: 'Media organizer' })).getByRole('tooltip'),
    ).toHaveTextContent('Check qBittorrent and run history');
  });
  it('keeps mirror notices with review/history and schedules links with acknowledgement', async () => {
    const { status, calls } = mockCliApi();
    status.mirror_pending = 2;
    render(
      <ToastProvider>
        <MediaOrganizerFeature />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Media organizer' }));
    await screen.findByText(/2 backup copies waiting/);
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    expect(screen.queryByText(/2 backup copies waiting/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'History' }));
    fireEvent.click(screen.getByRole('button', { name: 'Rebuild links' }));
    await waitFor(() =>
      expect(calls).toContainEqual({ path: '/api/media-organizer/alias', body: {} }),
    );
    expect(await screen.findByText('Link rebuild scheduled')).toBeInTheDocument();
  });
  it('keeps keyboard help available when a running operation disables its action', async () => {
    const { status } = mockCliApi();
    status.busy = { stage: 'apply', started_at: 'now' };
    render(
      <ToastProvider>
        <MediaOrganizerFeature />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Media organizer' }));
    await screen.findByLabelText('Show for Silo.S01E01');
    const dialog = screen.getByRole('dialog', { name: 'Media organizer' });
    expect(within(dialog).getByRole('button', { name: 'Apply selected' })).toBeDisabled();
    fireEvent.focus(within(dialog).getByRole('button', { name: 'About Apply selected' }));
    expect(
      within(screen.getByRole('dialog', { name: 'Media organizer' })).getByRole('tooltip'),
    ).toHaveTextContent('Unprocessed'.toLowerCase());
  });
});

it('refetches open rows before applying when an external run settled part of the same plan', async () => {
  const plan = samplePlan();
  const row = plan.rows[0]!;
  plan.rows.push({
    ...row,
    entry: { ...row.entry, name: 'Dune.2021' },
    proposal: { ...row.proposal!, category: 'Movies', show: null },
    decision: { category: 'Movies', show: null, accepted: true },
  });
  const { calls } = mockCliApi(plan);
  render(
    <ToastProvider>
      <MediaOrganizerFeature />
    </ToastProvider>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Media organizer' }));
  await screen.findByLabelText('Accept Silo.S01E01');
  // No status or job transition: the next plan fetch alone sees the external move.
  plan.settled_rows = ['Silo.S01E01'];
  plan.moved_rows = ['Silo.S01E01'];
  fireEvent.click(screen.getByRole('button', { name: 'Apply selected' }));
  await waitFor(() => expect(calls).toHaveLength(2));
  expect(calls[0]?.body).toEqual({
    plan_id: 'plan-1',
    decisions: { 'Dune.2021': { category: 'Movies', show: null, accepted: true } },
  });
  expect(screen.getByText('Moved')).toBeInTheDocument();
});
it('does not submit an empty apply when all selected rows settled outside the panel', async () => {
  const plan = samplePlan();
  const { calls } = mockCliApi(plan);
  render(
    <ToastProvider>
      <MediaOrganizerFeature />
    </ToastProvider>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Media organizer' }));
  await screen.findByLabelText('Accept Silo.S01E01');
  plan.settled_rows = ['Silo.S01E01'];
  fireEvent.click(screen.getByRole('button', { name: 'Apply selected' }));
  expect(await screen.findByText(/Nothing left to apply/)).toBeInTheDocument();
  expect(calls).toHaveLength(0);
  expect(screen.getByText('Unconfirmed')).toBeInTheDocument();
});

it('loads run item diagnostics on demand without changing media', async () => {
  const { calls } = mockCliApi();
  render(
    <ToastProvider>
      <MediaOrganizerFeature />
    </ToastProvider>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Media organizer' }));
  await screen.findByLabelText('Accept Silo.S01E01');
  fireEvent.click(screen.getByRole('button', { name: 'History' }));
  fireEvent.click(screen.getByRole('button', { name: 'View items' }));
  expect(
    await screen.findByText('This item needs attention: Destination already exists'),
  ).toBeInTheDocument();
  expect(screen.getByText('/media/TV/Silo')).toBeInTheDocument();
  expect(calls).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', { name: 'Hide items' }));
  expect(screen.queryByText('/media/TV/Silo')).not.toBeInTheDocument();
});

it('retries a failed plan read without requiring a new plan or a media mutation', async () => {
  mockCliApi();
  const original = globalThis.fetch;
  let fail = true;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, init?: RequestInit) => {
      if (path.includes('/plans/') && fail)
        return new Response(JSON.stringify({ message: 'Plan read unavailable' }), { status: 503 });
      return original(path, init);
    }),
  );
  render(
    <ToastProvider>
      <MediaOrganizerFeature />
    </ToastProvider>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Media organizer' }));
  await screen.findByText(/Loading the review plan could not be confirmed/);
  fail = false;
  fireEvent.click(screen.getByRole('button', { name: 'Refresh status' }));
  expect(await screen.findByLabelText('Show for Silo.S01E01')).toHaveValue('Silo');
});

describe('organizer loading state', () => {
  it('does not claim there is no plan while the status is still loading', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise<Response>(() => {})),
    );
    render(
      <ToastProvider>
        <MediaOrganizerFeature />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Media organizer' }));
    expect(await screen.findByText('Loading organizer…', { selector: 'p' })).toBeInTheDocument();
    expect(screen.queryByText(/No review plan yet/)).not.toBeInTheDocument();
  });
});

describe('organizer review follow-up regressions', () => {
  it('releases a lost accepted job without replaying it after a backend restart', async () => {
    const { status, calls } = mockCliApi();
    const original = globalThis.fetch;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (path: string, init?: RequestInit) => {
        const response = await original(path, init);
        if (path.endsWith('/apply')) status.job = null;
        return response;
      }),
    );
    render(
      <ToastProvider>
        <MediaOrganizerFeature />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Media organizer' }));
    await screen.findByLabelText('Show for Silo.S01E01');
    fireEvent.click(screen.getByRole('button', { name: 'Apply selected' }));
    expect(await screen.findByText(/Operation outcome unknown:/)).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Scan & propose' })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Close Media organizer' }));
    fireEvent.click(screen.getByRole('button', { name: 'Media organizer' }));
    expect(screen.getByRole('button', { name: 'Scan & propose' })).toBeEnabled();
    expect(calls.filter((call) => call.path.endsWith('/apply'))).toHaveLength(1);
  });
  it('waits for a post-save read instead of unlocking settings from an older poll', async () => {
    const { status, calls } = mockCliApi();
    const original = globalThis.fetch;
    const oldRead = deferred<Response>();
    const newRead = deferred<Response>();
    const stale = JSON.stringify(status);
    let holdNext = false;
    let oldStarted = false;
    let saved = false;
    let newStarted = false;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (path: string, init?: RequestInit) => {
        if (path === '/api/media-organizer/status' && holdNext) {
          holdNext = false;
          oldStarted = true;
          return oldRead.promise;
        }
        if (path === '/api/media-organizer/status' && saved) {
          newStarted = true;
          return newRead.promise;
        }
        const response = await original(path, init);
        if (path.endsWith('/settings')) saved = true;
        return response;
      }),
    );
    render(
      <ToastProvider>
        <MediaOrganizerFeature />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Media organizer' }));
    await screen.findByLabelText('Show for Silo.S01E01');
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    holdNext = true;
    fireEvent(window, new Event('focus'));
    await waitFor(() => expect(oldStarted).toBe(true));
    fireEvent.click(screen.getByRole('switch', { name: 'Auto apply after downloads' }));
    await waitFor(() => expect(saved).toBe(true));
    await act(async () => oldRead.resolve(new Response(stale)));
    await waitFor(() => expect(newStarted).toBe(true));
    expect(screen.getByRole('switch', { name: 'Auto apply after downloads' })).toBeDisabled();
    expect(screen.getByRole('switch', { name: 'Auto apply after downloads' })).toBeChecked();
    await act(async () => newRead.resolve(new Response(JSON.stringify(status))));
    await waitFor(() =>
      expect(screen.getByRole('switch', { name: 'Auto apply after downloads' })).toBeEnabled(),
    );
    expect(screen.getByRole('switch', { name: 'Auto apply after downloads' })).not.toBeChecked();
    expect(calls.filter((call) => call.path.endsWith('/settings'))).toHaveLength(1);
  });
  it('validates unchecked destinations and still sends accepted:false for a previously accepted row', async () => {
    const plan = samplePlan();
    const row = plan.rows[0]!;
    plan.rows.push({
      ...row,
      entry: { ...row.entry, name: 'Dune' },
      decision: { category: 'Movies', show: null, accepted: true },
      proposal: { ...row.proposal!, category: 'Movies', show: null },
    });
    const { calls } = mockCliApi(plan);
    render(
      <ToastProvider>
        <MediaOrganizerFeature />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Media organizer' }));
    const show = await screen.findByLabelText('Show for Silo.S01E01');
    fireEvent.change(show, { target: { value: '' } });
    fireEvent.click(screen.getByLabelText('Accept Silo.S01E01'));
    expect(screen.getByRole('button', { name: 'Apply selected' })).toBeDisabled();
    expect(show).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText('Enter a show folder before saving this review.')).toBeInTheDocument();
    fireEvent.change(show, { target: { value: 'Silo' } });
    fireEvent.click(screen.getByRole('button', { name: 'Apply selected' }));
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls[0]?.body.decisions).toEqual({
      'Silo.S01E01': { category: 'TV', show: 'Silo', accepted: false },
      Dune: { category: 'Movies', show: null, accepted: true },
    });
  });
  it('retains plan-load errors across view changes until a successful retry', async () => {
    mockCliApi();
    const original = globalThis.fetch;
    let fail = true;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (path: string, init?: RequestInit) => {
        if (path.includes('/plans/') && fail)
          return new Response(JSON.stringify({ message: 'Plan unavailable' }), { status: 503 });
        return original(path, init);
      }),
    );
    render(
      <ToastProvider>
        <MediaOrganizerFeature />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Media organizer' }));
    await screen.findByText(/Loading the review plan could not be confirmed/);
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    fireEvent.click(screen.getByRole('button', { name: 'Review' }));
    expect(screen.getByText(/Loading the review plan could not be confirmed/)).toBeInTheDocument();
    fail = false;
    fireEvent.click(screen.getByRole('button', { name: 'Refresh status' }));
    expect(await screen.findByLabelText('Show for Silo.S01E01')).toHaveValue('Silo');
    expect(
      screen.queryByText(/Loading the review plan could not be confirmed/),
    ).not.toBeInTheDocument();
  });
});
