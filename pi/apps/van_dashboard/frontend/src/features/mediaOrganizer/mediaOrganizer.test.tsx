import { useState } from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { decode } from '../../api/schema';
import { ToastProvider } from '../../components/ToastProvider';
import { ServiceLink, ServiceLinkTile } from '../../components/Tile';
import { ReviewTable } from './ReviewTable';
import { RunHistory } from './RunHistory';
import { SettingsForm } from './SettingsForm';
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

function mockCliApi() {
  const status = sampleStatus();
  const plan = samplePlan();
  const calls: { path: string; body: Record<string, unknown> }[] = [];
  const fetch = vi.fn(async (path: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) : {};
    if (init?.method === 'POST') calls.push({ path, body });
    let result: unknown = status;
    if (path.includes('/plans/') || path.endsWith('/decide')) result = plan;
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
    expect(screen.getByText('Skipped: incomplete')).toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(container.querySelector('.organizer-review__row--skipped')).not.toBeNull();
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
    expect(container.querySelector('article')).toHaveTextContent('Links: scheduled');
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
    expect(await screen.findByText('Applying…')).toBeInTheDocument();
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
