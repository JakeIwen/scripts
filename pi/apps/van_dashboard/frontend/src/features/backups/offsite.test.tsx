import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { decodeOffsiteResponse } from './offsite';
import { offsiteRemotePayload, offsiteSeedingPayload, offsiteV1Payload } from './offsiteFixtures';
import { OffsiteSection } from './OffsiteSection';

const NOW_MS = 1_700_000_100_000;

beforeAll(() => {
  HTMLDialogElement.prototype.showModal ??= function showModal() {
    this.setAttribute('open', '');
  };
  HTMLDialogElement.prototype.close ??= function close() {
    this.removeAttribute('open');
    this.dispatchEvent(new Event('close'));
  };
});

function json(body: unknown) {
  return new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } });
}

/** Serve `status` for the offsite endpoint and `rates` for any rates request. */
function serve(status: unknown, rates: unknown = { ok: true, supported: false, samples: [] }) {
  const fetchMock = vi.fn((input: RequestInfo | URL) =>
    Promise.resolve(json(String(input).endsWith('/rates') ? rates : status)),
  );
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function summary() {
  return document.querySelector('summary') as HTMLElement;
}

describe('offsite payload decoder', () => {
  it('reads a v2 payload', () => {
    const status = decodeOffsiteResponse(offsiteSeedingPayload());
    expect(status.historySupported).toBe(true);
    expect(status.run.source).toBe('exfat512');
    expect(status.history['FORT-MPLS-5TB']?.borg?.lastOk?.snapshot).toBe('99887766aabbccdd');
    expect(status.targetDrive?.mounted).toBe(true);
  });

  it('treats a v1 payload as current-run only', () => {
    const status = decodeOffsiteResponse(offsiteV1Payload());
    expect(status.historySupported).toBe(false);
    expect(status.history).toEqual({});
  });

  it('rejects an unknown result and a missing ok flag', () => {
    const bad = offsiteSeedingPayload();
    (bad.status as { result: string }).result = 'weird';
    expect(() => decodeOffsiteResponse(bad)).toThrow(TypeError);
    expect(() => decodeOffsiteResponse({ ...offsiteSeedingPayload(), ok: false })).toThrow(
      TypeError,
    );
  });
});

describe('OffsiteSection', () => {
  beforeEach(() => {
    vi.spyOn(Date, 'now').mockReturnValue(NOW_MS);
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('is collapsed by default but shows the status in its summary chip', async () => {
    serve(offsiteSeedingPayload());
    render(<OffsiteSection open />);
    expect(document.querySelector('details')?.open).toBe(false);
    expect(within(summary()).getByRole('heading', { name: 'Offsite NAS' })).toBeTruthy();
    expect(await within(summary()).findByText('Seeding 21% · EXFAT512')).toBeTruthy();
  });

  it('shows the main card first, then the four sources in run order', async () => {
    serve(offsiteSeedingPayload());
    render(<OffsiteSection open />);
    expect(await screen.findByText('Local seed · FORT-MPLS-5TB')).toBeTruthy();
    expect(screen.getByRole('progressbar', { name: 'Offsite backup progress' })).toBeTruthy();
    expect(screen.getByText('Logical rate')).toBeTruthy();
    expect(screen.getByText(/2\.0 TiB of 5\.0 TiB used/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'FORT-MPLS-5TB drive I/O history' })).toBeTruthy();

    const cards = [...document.querySelectorAll('.backup-evidence-grid > article h4')];
    expect(cards.map((node) => node.textContent)).toEqual([
      'EXFAT512',
      'Moving Parts',
      'Borg repository',
      'Time Machine',
    ]);
  });

  it('gives only the running source live progress and others their last outcome', async () => {
    serve(offsiteSeedingPayload());
    render(<OffsiteSection open />);
    await screen.findByText('Local seed · FORT-MPLS-5TB');
    expect(screen.getByRole('progressbar', { name: 'EXFAT512 progress' })).toBeTruthy();
    expect(screen.queryByRole('progressbar', { name: 'Borg repository progress' })).toBeNull();
    const borg = screen.getByRole('heading', { name: 'Borg repository' }).closest('article')!;
    expect(within(borg).getByText('Failed')).toBeTruthy();
    expect(within(borg).getByText('repository locked')).toBeTruthy();
    expect(within(borg).getByText(/Last OK 2d ago · 99887766/)).toBeTruthy();
    expect(within(borg).getByText('abcdef01')).toBeTruthy();
    const timeMachine = screen.getByRole('heading', { name: 'Time Machine' }).closest('article')!;
    expect(within(timeMachine).getByText('Not run yet')).toBeTruthy();
  });

  it('falls back to the current run alone while the Pi has the v1 script', async () => {
    serve(offsiteV1Payload());
    render(<OffsiteSection open />);
    expect(await within(summary()).findByText('Last run OK · 1h ago')).toBeTruthy();
    const exfat = screen.getByRole('heading', { name: 'EXFAT512' }).closest('article')!;
    expect(within(exfat).getByText('OK')).toBeTruthy();
    expect(within(exfat).getByText('12345678')).toBeTruthy();
    const borg = screen.getByRole('heading', { name: 'Borg repository' }).closest('article')!;
    expect(within(borg).getByText('No record in current run')).toBeTruthy();
    expect(screen.getByText(/updated offsite script/)).toBeTruthy();
  });

  it('labels a remote target and shows receipts and evidence instead of drive usage', async () => {
    serve(offsiteRemotePayload());
    render(<OffsiteSection open />);
    expect(await screen.findByText('Synology over Tailscale · NAS-14TB')).toBeTruthy();
    expect(screen.getByText('OK · 1d ago')).toBeTruthy();
    expect(screen.getByText('Failed · 2d ago · file mismatch')).toBeTruthy();
    expect(screen.queryByText('Target drive')).toBeNull();
    expect(screen.queryByRole('button', { name: /drive I\/O/ })).toBeNull();
    expect(screen.getByText('feedface')).toBeTruthy();
  });

  it('opens a source’s rate history and explains an empty one', async () => {
    const fetchMock = serve(offsiteSeedingPayload());
    render(<OffsiteSection open />);
    await screen.findByText('Local seed · FORT-MPLS-5TB');
    const borg = screen.getByRole('heading', { name: 'Borg repository' }).closest('article')!;
    fireEvent.click(within(borg).getByRole('button', { name: 'Processing rate graph' }));
    expect(
      await screen.findByText('History starts with the next run after the Pi update.'),
    ).toBeTruthy();
    expect(fetchMock.mock.calls.map((call) => String(call[0]))).toContain(
      '/api/backups/offsite/borg/rates',
    );
  });

  it('draws the history when the Pi has samples', async () => {
    const samples = Array.from({ length: 5 }, (_, index) => [
      1_700_000_000 - 3600 + index * 60,
      1_048_576,
    ]);
    serve(offsiteSeedingPayload(), { ok: true, supported: true, samples });
    render(<OffsiteSection open />);
    await screen.findByText('Local seed · FORT-MPLS-5TB');
    const borg = screen.getByRole('heading', { name: 'Borg repository' }).closest('article')!;
    fireEvent.click(within(borg).getByRole('button', { name: 'Processing rate graph' }));
    expect(await screen.findByRole('img', { name: /Average processing rate/ })).toBeTruthy();
  });

  it('does not poll while the Backups sheet is closed', async () => {
    const fetchMock = serve(offsiteSeedingPayload());
    render(<OffsiteSection open={false} />);
    await waitFor(() => expect(screen.getByText('Loading')).toBeTruthy());
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('shows an error when the endpoint fails and nothing was loaded', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ ok: false, message: 'Offsite backup status is unavailable.' }),
          {
            status: 503,
            headers: { 'Content-Type': 'application/json' },
          },
        ),
      ),
    );
    render(<OffsiteSection open />);
    expect(await screen.findByText('Offsite backup status is unavailable.')).toBeTruthy();
    expect(within(summary()).getByText('Unavailable')).toBeTruthy();
  });
  it('polls every 10 s while running and every 30 s otherwise', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      for (const [payload, interval] of [
        [offsiteSeedingPayload(), 10_000],
        [offsiteV1Payload(), 30_000],
      ] as const) {
        const fetchMock = serve(payload);
        const view = render(<OffsiteSection open />);
        await screen.findAllByText(/Local seed|Last run OK|Seeding/);
        // Switching to the running interval restarts the poll once, which is a fresh read.
        await act(() => vi.advanceTimersByTimeAsync(0));
        const settled = fetchMock.mock.calls.length;
        await act(() => vi.advanceTimersByTimeAsync(interval - 1_000));
        expect(fetchMock).toHaveBeenCalledTimes(settled);
        await act(() => vi.advanceTimersByTimeAsync(2_000));
        expect(fetchMock).toHaveBeenCalledTimes(settled + 1);
        view.unmount();
      }
    } finally {
      vi.useRealTimers();
    }
  });
});
