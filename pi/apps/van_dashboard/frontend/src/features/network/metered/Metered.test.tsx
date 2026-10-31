import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MeteredSection } from './MeteredSection';
import { MeteredConsumers } from './MeteredConsumers';
import type { ConsumerState, MeteredStatus } from './schema';
import { meteredFixture } from './testFixtures';

let status: MeteredStatus;
let requests: { path: string; body: Record<string, unknown> }[];
let conflict: boolean;
let gets: number;

beforeEach(() => {
  status = meteredFixture();
  requests = [];
  conflict = false;
  gets = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, init?: RequestInit) => {
      if (init?.method !== 'POST') {
        gets += 1;
        return new Response(JSON.stringify(status));
      }
      expect(init.headers).toMatchObject({
        'X-Van-Dashboard': '1',
        'Content-Type': 'application/json',
      });
      const body = JSON.parse(String(init.body));
      requests.push({ path, body });
      if (conflict) {
        if (path.endsWith('/policy')) status.policy.revision += 1;
        else status.config.revision += 1;
        return new Response(JSON.stringify({ ok: false, message: 'revision conflict' }), {
          status: 409,
        });
      }
      return new Response(
        JSON.stringify({ ok: true, config: status.config, policy: status.policy }),
      );
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function openSection() {
  render(<MeteredSection open />);
  await screen.findByRole('button', { name: 'Add SSID' });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Add SSID' })).toBeEnabled());
}

async function saved() {
  await screen.findByText('Saved.');
  await waitFor(() => expect(screen.getByRole('button', { name: 'Add SSID' })).toBeEnabled());
}

describe('metered controls', () => {
  it('shows the verdict, matched member and empty read-only Mac state', async () => {
    await openSection();
    expect(screen.getByText('metered SSID selected')).toBeInTheDocument();
    expect(screen.getByText('wan · denlink')).toBeInTheDocument();
    expect(screen.getByText('No Mac report received yet.')).toBeInTheDocument();
    expect(screen.getByText('Change these on the Mac Panel')).toBeInTheDocument();
    expect(screen.getByText('Advanced').closest('details')).not.toHaveAttribute('open');
  });

  it.each([
    ['3600', { value: 'until', seconds: 3600 }],
    ['14400', { value: 'until', seconds: 14400 }],
    ['episode', { value: 'episode' }],
  ])('posts an approval for %s and then revokes', async (value, approval) => {
    await openSection();
    fireEvent.change(screen.getByLabelText('Approve Torrents'), { target: { value } });
    await saved();
    expect(requests[0]).toEqual({
      path: '/api/metered/policy',
      body: { op: 'approve', revision: 3, consumer: 'pi.torrents', ...approval },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Revoke Torrents' }));
    await saved();
    expect(requests[1]?.body).toEqual({ op: 'revoke', revision: 3, consumer: 'pi.torrents' });
  });

  it('adds and removes exact case-sensitive SSIDs without trimming', async () => {
    await openSection();
    fireEvent.change(screen.getByLabelText('SSID'), { target: { value: ' Cafe WiFi ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add SSID' }));
    await saved();
    expect(requests[0]).toEqual({
      path: '/api/metered/ssids',
      body: { op: 'add', ssid: ' Cafe WiFi ', revision: 2 },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Remove SSID denlink' }));
    await saved();
    expect(requests[1]?.body).toEqual({ op: 'remove', ssid: 'denlink', revision: 2 });
  });

  it('refetches on a 409 without replaying the remove', async () => {
    await openSection();
    conflict = true;
    fireEvent.click(screen.getByRole('button', { name: 'Remove SSID denlink' }));
    await screen.findByText(/Refetched current settings; review and try again/);
    expect(gets).toBe(2);
    expect(requests).toHaveLength(1);
    conflict = false;
    fireEvent.click(screen.getByRole('button', { name: 'Remove SSID denlink' }));
    await saved();
    expect(requests[1]?.body.revision).toBe(3);
  });

  it('refetches policy conflicts as well', async () => {
    await openSection();
    conflict = true;
    fireEvent.click(screen.getByRole('button', { name: 'Revoke Torrents' }));
    await screen.findByText(/Refetched current settings/);
    expect(requests).toHaveLength(1);
    expect(gets).toBe(2);
    conflict = false;
    fireEvent.click(screen.getByRole('button', { name: 'Revoke Torrents' }));
    await saved();
    expect(requests[1]?.body.revision).toBe(4);
  });

  it('rejects empty, overlong UTF-8 and control-character SSIDs', async () => {
    await openSection();
    for (const value of ['', 'é'.repeat(17), 'bad\u007fssid']) {
      fireEvent.change(screen.getByLabelText('SSID'), { target: { value } });
      fireEvent.click(screen.getByRole('button', { name: 'Add SSID' }));
      expect(screen.getByRole('alert')).toBeInTheDocument();
    }
    expect(requests).toHaveLength(0);
  });

  it('requires a positive unmetered duration no longer than 24h and posts mode and exemptions', async () => {
    await openSection();
    fireEvent.click(screen.getByText('Advanced'));
    fireEvent.change(screen.getByLabelText('Pi mode'), { target: { value: 'unmetered' } });
    for (const value of ['', '0', '-1', '25']) {
      fireEvent.change(screen.getByLabelText('Duration (hours)'), { target: { value } });
      fireEvent.click(screen.getByRole('button', { name: 'Apply mode' }));
      expect(screen.getByRole('alert')).toHaveTextContent('at most 24 hours');
    }
    expect(requests).toHaveLength(0);
    fireEvent.change(screen.getByLabelText('Duration (hours)'), { target: { value: '24' } });
    fireEvent.click(screen.getByRole('button', { name: 'Apply mode' }));
    await saved();
    expect(requests[0]?.body).toEqual({
      op: 'mode',
      revision: 3,
      value: 'unmetered',
      seconds: 86400,
    });
    fireEvent.change(screen.getByLabelText('Pi mode'), { target: { value: 'auto' } });
    fireEvent.click(screen.getByRole('button', { name: 'Apply mode' }));
    await saved();
    expect(requests[1]?.body).toEqual({ op: 'mode', revision: 3, value: 'auto' });
    fireEvent.click(screen.getByRole('checkbox', { name: 'Torrents' }));
    await saved();
    expect(requests[2]?.body).toEqual({
      op: 'exempt',
      revision: 3,
      consumer: 'pi.torrents',
      enabled: true,
    });
  });

  it('posts metered mode with a duration or without an expiry', async () => {
    await openSection();
    fireEvent.click(screen.getByText('Advanced'));
    fireEvent.change(screen.getByLabelText('Pi mode'), { target: { value: 'metered' } });
    fireEvent.click(screen.getByRole('button', { name: 'Apply mode' }));
    await saved();
    expect(requests[0]?.body).toEqual({ op: 'mode', revision: 3, value: 'metered', seconds: 3600 });
    fireEvent.change(screen.getByLabelText('Duration (hours)'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Apply mode' }));
    await saved();
    expect(requests[1]?.body).toEqual({ op: 'mode', revision: 3, value: 'metered' });
  });

  it('shows Unknown distinctly from a metered verdict', async () => {
    status.state.verdict.metered = null;
    status.state.verdict.reason = 'selected uplink identity is unknown';
    await openSection();
    expect(
      screen.getByText('Unknown', { selector: '.status-pill span' }).closest('.status-pill'),
    ).toHaveClass('status-pill--neutral');
    expect(screen.getByText('selected uplink identity is unknown')).toBeInTheDocument();
  });

  it('does not claim current Pi permission from stale or revision-mismatched state', async () => {
    status.state.effective.metered = false;
    status.state.consumers['pi.torrents']!.permitted = true;
    status.state.valid_until = 0;
    await openSection();
    const row = within(screen.getByRole('article', { name: 'Torrents' }));
    expect(row.getAllByText('Unknown')).toHaveLength(2);
    expect(screen.getByText('Evaluation is stale or awaiting reconciliation')).toBeInTheDocument();
  });

  it('shows each Mac row and report age without controls', async () => {
    status.mac_report = {
      ...status.state,
      host: 'mac',
      evaluated_at: status.state.evaluated_at - 120,
      consumers: {
        'mac.icloud': { permitted: false, reason: 'metered', approval: null, actual: 'n/a' },
      },
    };
    await openSection();
    expect(screen.getByText(/Reported 2m ago · stale/)).toBeInTheDocument();
    const row = screen.getByRole('article', { name: 'Mac iCloud' });
    expect(within(row).getByText('Advisory')).toBeInTheDocument();
    expect(within(row).queryByRole('combobox')).not.toBeInTheDocument();
    expect(within(row).queryByRole('button')).not.toBeInTheDocument();
  });
});

describe('consumer labels in rendered rows', () => {
  const approval = {
    kind: 'until' as const,
    until: 2000,
    episode: null,
    set_by: 'dashboard',
    set_at: 1000,
  };
  it.each<[string, Partial<ConsumerState>, boolean]>([
    ['Held', { permitted: false }, true],
    ['Permitted (unmetered)', { permitted: true, actual: 'running' }, false],
    [
      'Permitted for this metered episode',
      {
        permitted: true,
        approval: { ...approval, kind: 'episode', until: null, episode: 'episode-1' },
      },
      true,
    ],
    ['Permitted, but stopped manually', { permitted: true, actual: 'stopped_manually' }, true],
    ['Permitted (not gated)', { permitted: true, reason: 'not gated (explicit exemption)' }, true],
  ])('shows %s separately from actual', (label, overrides, metered) => {
    const fixture = meteredFixture();
    fixture.state.effective.metered = metered;
    Object.assign(fixture.state.consumers['pi.torrents']!, overrides);
    render(<MeteredConsumers consumers={[fixture.registry[0]!]} state={fixture.state} />);
    const permission = screen.getByText('Permission').nextElementSibling;
    expect(permission).toHaveTextContent(label);
    expect(screen.getByText('Actual').nextElementSibling).not.toBeNull();
  });

  it.each<[ConsumerState['actual'], string]>([
    ['running', 'Running'],
    ['held', 'Held'],
    ['stopped_manually', 'Stopped manually'],
    ['unknown', 'Unknown'],
    ['n/a', 'Not applicable'],
  ])('shows actual %s independently of permission', (actual, label) => {
    const fixture = meteredFixture();
    fixture.state.consumers['pi.torrents']!.actual = actual;
    render(<MeteredConsumers consumers={[fixture.registry[0]!]} state={fixture.state} />);
    expect(screen.getByText('Actual').nextElementSibling).toHaveTextContent(label);
  });

  it('shows a timed approval as HH:MM', () => {
    const fixture = meteredFixture();
    fixture.state.consumers['pi.torrents'] = {
      permitted: true,
      actual: 'running',
      reason: 'temporary approval',
      approval,
    };
    render(<MeteredConsumers consumers={[fixture.registry[0]!]} state={fixture.state} />);
    expect(screen.getByText(/^Permitted until \d\d:\d\d$/)).toBeInTheDocument();
  });

  it('shows missing or stale states as Unknown', () => {
    const fixture = meteredFixture();
    fixture.state.consumers = {};
    render(<MeteredConsumers consumers={[fixture.registry[0]!]} state={fixture.state} stale />);
    expect(screen.getAllByText('Unknown')).toHaveLength(2);
  });

  it('shows Unprotected with its note, not an enforcement claim', () => {
    const fixture = meteredFixture();
    render(<MeteredConsumers consumers={[fixture.registry[2]!]} state={fixture.state} />);
    expect(screen.getByText('Unprotected')).toBeInTheDocument();
    expect(screen.getAllByText('Updater integration is pending.').length).toBeGreaterThan(0);
  });
});
