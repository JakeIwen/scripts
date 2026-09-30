import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import { fetchNetworkHistory } from './api';
import { decodeNetworkHistory } from './decoders';
import { NetworkHistoryFeature } from './NetworkHistoryFeature';
import { HistoryFilters, HistoryReport, IncidentDetail } from './NetworkHistoryView';
import { detailFixture, historyFixture } from './testFixtures';

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
  vi.useRealTimers();
  window.history.replaceState(null, '', '/');
});

function response(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('network history evidence view', () => {
  it('shows RAM evidence and missing durable history warnings without expanding source details', () => {
    const report = historyFixture();
    report.coverage.push({
      source: 'storage',
      device: 'vanpi',
      status: 'unknown',
      last_event_at: null,
      last_received_at: null,
      detail:
        'USB storage unavailable. Recorder is using RAM; evidence is lost on reboot. Older flash history is unavailable.',
    });
    const view = render(<HistoryReport report={report} onIncident={vi.fn()} />);
    const warning = screen.getByRole('status', { name: 'Recorder storage warning' });
    expect(warning).toBeVisible();
    expect(warning).toHaveTextContent('using RAM; evidence is lost on reboot');
    expect(warning).toHaveTextContent('Older flash history is unavailable');
    expect(warning.closest('details')).toBeNull();
    view.rerender(
      <HistoryReport
        report={{
          ...report,
          coverage: report.coverage.map((source) => ({ ...source, status: 'current' })),
        }}
        onIncident={vi.fn()}
      />,
    );
    expect(
      screen.queryByRole('status', { name: 'Recorder storage warning' }),
    ).not.toBeInTheDocument();
  });

  it('keeps the RAM warning visible when viewing an incident directly', () => {
    const detail = detailFixture();
    detail.coverage.push({
      source: 'storage',
      device: 'vanpi',
      status: 'unknown',
      last_event_at: null,
      last_received_at: null,
      detail: 'Volatile RAM recording; evidence is lost on reboot.',
    });
    render(<IncidentDetail detail={detail} />);
    expect(screen.getByRole('status', { name: 'Recorder storage warning' })).toHaveTextContent(
      'lost on reboot',
    );
  });

  it('preserves full-range counts, timing uncertainty, provenance and source staleness', () => {
    const selected = vi.fn();
    render(<HistoryReport report={historyFixture()} onIncident={selected} />);
    expect(screen.getByText(/1200 observations · 1 incidents/)).toBeInTheDocument();
    expect(screen.getByText(/Showing 1 of 1200 observations/)).toBeInTheDocument();
    expect(screen.getByText('stale')).toBeInTheDocument();
    expect(screen.getByText(/exact cross-device order/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /External test destinations failed/ }));
    expect(selected).toHaveBeenCalledWith('incident-11');
    const observation = screen
      .getByText('gateway=reachable public_tests=failed password=[REDACTED]')
      .closest('summary')!;
    fireEvent.click(observation);
    const evidence = observation.closest('details')!;
    expect(within(evidence).getByText('Database row ID')).toBeInTheDocument();
    expect(within(evidence).getByText('fixture-physical-record-11')).toBeInTheDocument();
    expect(within(evidence).getByText('Device source time')).toBeInTheDocument();
    expect(within(evidence).getByText('Unknown')).toBeInTheDocument();
    expect(within(evidence).getByText('2023-11-14 22:16:40 UTC')).toBeInTheDocument();
    expect(screen.getByLabelText('Provenance for evidence 11')).toHaveTextContent(
      '"backfill": true',
    );
  });

  it('separates observations from hypotheses and exports the redacted bounded payload', () => {
    const create = vi.fn().mockReturnValue('blob:fixture');
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => undefined);
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: create, revokeObjectURL: vi.fn() }));
    render(<IncidentDetail detail={detailFixture()} />);
    expect(screen.getByRole('heading', { name: 'Observations' })).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: 'Hypotheses · not confirmed causes' }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Download redacted bundle' }));
    expect(create.mock.calls[0]?.[0]).toBeInstanceOf(Blob);
    expect(click).toHaveBeenCalledOnce();
  });

  it('applies range, uplink, device and text filters together', () => {
    const apply = vi.fn();
    render(<HistoryFilters query={{ hours: 6 }} report={historyFixture()} onApply={apply} />);
    fireEvent.change(screen.getByLabelText('Time range'), { target: { value: '24' } });
    fireEvent.change(screen.getByLabelText('Uplink'), { target: { value: 'clientwan' } });
    fireEvent.change(screen.getByLabelText('Device'), { target: { value: 'router' } });
    fireEvent.change(screen.getByLabelText('Search evidence'), { target: { value: 'DHCP lease' } });
    fireEvent.click(screen.getByRole('button', { name: 'Apply filters' }));
    expect(apply).toHaveBeenCalledWith({
      hours: 24,
      uplink: 'clientwan',
      device: 'router',
      search: 'DHCP lease',
    });
  });

  it('validates custom UTC windows and does not send an inverted range', () => {
    const apply = vi.fn();
    render(<HistoryFilters query={{ hours: 6 }} report={null} onApply={apply} />);
    fireEvent.change(screen.getByLabelText('Time range'), { target: { value: 'custom' } });
    fireEvent.change(screen.getByLabelText('Start (UTC)'), {
      target: { value: '2026-09-29T12:00' },
    });
    fireEvent.change(screen.getByLabelText('End (UTC)'), { target: { value: '2026-09-29T11:00' } });
    fireEvent.click(screen.getByRole('button', { name: 'Apply filters' }));
    expect(screen.getByRole('alert')).toHaveTextContent('increasing UTC interval');
    expect(apply).not.toHaveBeenCalled();
  });

  it('does not treat an empty report as proof of healthy connectivity', () => {
    const data = {
      ...historyFixture(),
      events: [],
      incidents: [],
      coverage: [],
      counts: { events: 0, incidents: 0 },
    };
    render(<HistoryReport report={data} onIncident={vi.fn()} />);
    expect(screen.getByText('No source coverage is available.')).toBeInTheDocument();
    expect(screen.getByText(/does not establish uninterrupted connectivity/)).toBeInTheDocument();
    expect(
      screen.getByText('No observations match this interval and these filters.'),
    ).toBeInTheDocument();
  });
});

describe('network history transport and outage behavior', () => {
  it('flags storage fallback on the dashboard tile even with the history sheet closed', async () => {
    const report = historyFixture();
    report.generated_at = Date.now() / 1000;
    report.coverage = [
      {
        source: 'storage',
        device: 'vanpi',
        status: 'unknown',
        last_event_at: null,
        last_received_at: null,
        detail: 'Volatile RAM recording; evidence is lost on reboot.',
      },
    ];
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(response({ ...report, ok: true }));
    render(<NetworkHistoryFeature />);
    expect(await screen.findByText('Storage warning')).toBeVisible();
    expect(screen.queryByText('Recording')).not.toBeInTheDocument();
  });

  it('ages the previous report even while the next request never settles', async () => {
    vi.useFakeTimers();
    const report = {
      ...historyFixture(),
      generated_at: Date.now() / 1000,
      coverage: [{ ...historyFixture().coverage[0]!, status: 'current' }],
    };
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(response({ ...report, ok: true }))
      .mockImplementation(() => new Promise<Response>(() => undefined));
    render(<NetworkHistoryFeature />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText('Recording')).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(105_000);
    });
    expect(screen.getByText('Stale report')).toBeInTheDocument();
    expect(screen.queryByText('Recording')).not.toBeInTheDocument();
  });

  it('uses only the local read-only API and preserves literal search strings', async () => {
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(response({ ...historyFixture(), ok: true }));
    await fetchNetworkHistory({ hours: 24, search: 'DNS & DHCP', device: 'router' });
    expect(fetch).toHaveBeenCalledWith(
      '/api/network-history?hours=24&search=DNS+%26+DHCP&device=router',
      { cache: 'no-store', signal: undefined },
    );
  });

  it('rejects an unsupported schema or missing provenance rather than rendering a partial healthy report', () => {
    expect(() =>
      decodeNetworkHistory({ ...historyFixture(), ok: true, schema_version: 99 }),
    ).toThrow();
    const report = historyFixture();
    expect(() =>
      decodeNetworkHistory({
        ...report,
        ok: true,
        events: [{ ...report.events[0], provenance: null }],
      }),
    ).toThrow();
  });

  it('deep links directly to a selected interval and underlying incident evidence', async () => {
    window.history.replaceState(
      null,
      '',
      '/#network-history?hours=24&uplink=clientwan&incident=incident-11',
    );
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async (path) =>
        String(path).includes('/incidents/')
          ? response(detailFixture().raw)
          : response({ ...historyFixture(), ok: true }),
      );
    render(<NetworkHistoryFeature />);
    await screen.findByRole('region', { name: 'Incident evidence' });
    expect(fetch).toHaveBeenCalledWith(
      '/api/network-history?hours=24&uplink=clientwan',
      expect.anything(),
    );
    expect(fetch).toHaveBeenCalledWith(
      '/api/network-history/incidents/incident-11',
      expect.anything(),
    );
    expect(screen.getByLabelText('Time range')).toHaveValue('24');
    expect(screen.getByLabelText('Uplink')).toHaveValue('clientwan');
  });

  it('marks retained data unavailable when refresh fails instead of silently retaining a healthy badge', async () => {
    window.history.replaceState(null, '', '/#network-history?hours=6');
    const report = {
      ...historyFixture(),
      generated_at: Date.now() / 1000,
      coverage: [{ ...historyFixture().coverage[0]!, status: 'current' }],
    };
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(response({ ...report, ok: true }))
      .mockRejectedValue(new TypeError('Local connection unavailable'));
    render(<NetworkHistoryFeature />);
    await screen.findByText('Recording');
    fireEvent.click(screen.getByRole('button', { name: 'Refresh evidence' }));
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(
        'last successful report remains visible and may be stale',
      ),
    );
    expect(screen.getByText('Unavailable')).toBeInTheDocument();
    expect(screen.queryByText('Recording')).not.toBeInTheDocument();
    expect(screen.getByText(/Showing 1 of 1200 observations/)).toBeInTheDocument();
    expect(
      fetch.mock.calls.every(([path]) => String(path).startsWith('/api/network-history')),
    ).toBe(true);
  });
});
