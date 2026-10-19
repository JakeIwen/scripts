import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { postForm } from '../../api/client';
import { ToastProvider } from '../../components/ToastProvider';
import type { PollingState } from '../../hooks/usePollingResource';
import { addDealWatchHavenoWatch, editDealWatchHavenoWatch } from './api';
import type { DealWatchControls } from './controls';
import { decodeDealWatchStatus } from './decoders';
import { DealWatchHavenoSection } from './DealWatchHavenoSection';
import { DealWatchTile } from './DealWatchTile';
import { havenoPercentLabel } from './presentation';
import { dealWatchPayload } from './testFixtures';
import type { DealWatchHavenoDraft, DealWatchStatus } from './types';

vi.mock('../../api/client', () => ({ getJson: vi.fn(), postForm: vi.fn() }));

const havenoDraft: DealWatchHavenoDraft = {
  thresholdPct: '-2.50',
  feePct: '0',
  paymentMethod: 'ZELLE',
  currency: 'USD',
  minXmr: '',
  maxXmr: '',
  title: '',
};

function controls(): DealWatchControls {
  return {
    busy: false,
    addListing: vi.fn().mockResolvedValue(true),
    editListing: vi.fn().mockResolvedValue(true),
    removeListing: vi.fn().mockResolvedValue(true),
    setListingMute: vi.fn().mockResolvedValue(true),
    checkListings: vi.fn().mockResolvedValue(true),
    addSearch: vi.fn().mockResolvedValue(true),
    removeSearch: vi.fn().mockResolvedValue(true),
    dismissSearchResult: vi.fn().mockResolvedValue(true),
    checkSearch: vi.fn().mockResolvedValue(true),
    addHavenoWatch: vi.fn().mockResolvedValue(true),
    editHavenoWatch: vi.fn().mockResolvedValue(true),
    removeHavenoWatch: vi.fn().mockResolvedValue(true),
    setHavenoMute: vi.fn().mockResolvedValue(true),
    checkHaveno: vi.fn().mockResolvedValue(true),
    saveSchedule: vi.fn().mockResolvedValue(true),
  };
}

function tileResource(data: DealWatchStatus): PollingState<DealWatchStatus> {
  return {
    data,
    error: null,
    initialLoading: false,
    refreshing: false,
    lastUpdatedAt: Date.now(),
    refresh: vi.fn().mockResolvedValue(data),
  };
}

beforeEach(() => {
  vi.mocked(postForm).mockReset();
  vi.mocked(postForm).mockResolvedValue({ ok: true, message: 'Saved' });
});

afterEach(cleanup);

describe('Haveno decoder', () => {
  it('accepts signed percentages and the absent-pair deployment fallback', () => {
    const decoded = decodeDealWatchStatus(dealWatchPayload());
    expect(decoded.havenoWatches[0]).toMatchObject({
      thresholdPct: '-2.00',
      feePct: '0.50',
      bestVsMarketPct: '-2.63',
      bestVsMarket24hPct: '-3.10',
    });
    expect(decoded.havenoWatches[0]?.offers).toMatchObject([
      { vsMarketPct: '-2.63', effectivePct: '-2.13' },
      { vsMarketPct: '0.00', effectivePct: '0.50', maxXmr: '2.5' },
      { vsMarketPct: '0.38', effectivePct: '0.88' },
    ]);

    for (const thresholdPct of ['0.00', '1.25']) {
      const signed = dealWatchPayload();
      (signed.haveno_watches as Record<string, unknown>[])[0]!.threshold_pct = thresholdPct;
      expect(decodeDealWatchStatus(signed).havenoWatches[0]?.thresholdPct).toBe(thresholdPct);
    }

    const nullableAmount = dealWatchPayload();
    const nullableOffers = (nullableAmount.haveno_watches as Record<string, unknown>[])[0]!
      .offers as Record<string, unknown>[];
    nullableOffers[1]!.min_xmr = null;
    expect(decodeDealWatchStatus(nullableAmount).havenoWatches[0]?.offers[1]?.minXmr).toBeNull();

    const legacy = dealWatchPayload();
    delete legacy.haveno_watches;
    delete legacy.haveno_summary;
    expect(decodeDealWatchStatus(legacy)).toMatchObject({
      havenoWatches: [],
      havenoSummary: { count: 0, checked: 0, matching: 0, errors: 0 },
    });
  });

  it('rejects missing fee_pct and malformed decimal, timestamp, URL, and enum fields', () => {
    const halfPresent = dealWatchPayload();
    delete halfPresent.haveno_summary;
    expect(() => decodeDealWatchStatus(halfPresent)).toThrow('must appear together');

    const missingFee = dealWatchPayload();
    delete (missingFee.haveno_watches as Record<string, unknown>[])[0]!.fee_pct;
    expect(() => decodeDealWatchStatus(missingFee)).toThrow('fee_pct');

    const cases: Array<[string, unknown]> = [
      ['best_price', '566.3'],
      ['best_vs_market_pct', '+2.63'],
      ['fee_pct', '-0.01'],
      ['max_xmr', '10.0'],
      ['reference_at', 1_725_100_300.5],
      ['reference_source', 'coinbase'],
      ['market_url', 'javascript:alert(1)'],
    ];
    for (const [field, value] of cases) {
      const malformed = dealWatchPayload();
      const watch = (malformed.haveno_watches as Record<string, unknown>[])[0]!;
      watch[field] = value;
      expect(() => decodeDealWatchStatus(malformed), field).toThrow();
    }
  });
});

describe('Haveno API', () => {
  it('posts exactly the seven add fields, including a negative threshold', async () => {
    await addDealWatchHavenoWatch(havenoDraft);
    expect(postForm).toHaveBeenCalledWith('/api/price-checks/haveno/add', {
      threshold_pct: '-2.50',
      fee_pct: '0',
      payment_method: 'ZELLE',
      currency: 'USD',
      min_xmr: '',
      max_xmr: '',
      title: '',
    });
  });

  it('posts the ID plus the same seven fields for edits', async () => {
    await editDealWatchHavenoWatch(21, havenoDraft);
    expect(postForm).toHaveBeenCalledWith('/api/price-checks/haveno/edit', {
      id: '21',
      threshold_pct: '-2.50',
      fee_pct: '0',
      payment_method: 'ZELLE',
      currency: 'USD',
      min_xmr: '',
      max_xmr: '',
      title: '',
    });
  });
});

describe('Haveno views', () => {
  it('shows the XMR summary, matching tone, and best offer on the tile', () => {
    const payload = dealWatchPayload();
    payload.items = [];
    payload.summary = { count: 0, checked: 0, below_threshold: 0, errors: 0 };
    const status = decodeDealWatchStatus(payload);
    const { container } = render(
      <DealWatchTile resource={tileResource(status)} onOpen={vi.fn()} />,
    );

    expect(screen.getByText('0 listings · 1 query · 1 XMR · 0 errors')).toBeInTheDocument();
    expect(screen.getByText('$566.32 · −2.63%')).toBeInTheDocument();
    expect(container.querySelector('.tile--good')).not.toBeNull();
  });

  it('formats positive, negative, and zero percentages in listing style', () => {
    expect(havenoPercentLabel('0.38')).toBe('+0.38%');
    expect(havenoPercentLabel('-2.63')).toBe('−2.63%');
    expect(havenoPercentLabel('0.00')).toBe('0.00%');
  });

  it('renders listed and fee-adjusted percentages without losing the match label', () => {
    const status = decodeDealWatchStatus(dealWatchPayload());
    render(
      <ToastProvider>
        <DealWatchHavenoSection
          watches={status.havenoWatches}
          activity={status.havenoActivity}
          disabled={false}
          controls={controls()}
        />
      </ToastProvider>,
    );

    expect(screen.getByRole('heading', { name: 'Haveno XMR offers' })).toBeInTheDocument();
    expect(screen.getByText('$566.32 · −2.63%')).toBeInTheDocument();
    expect(screen.getByText('−2.63% vs market')).toBeInTheDocument();
    expect(screen.getByText('−2.13% effective')).toBeInTheDocument();
    expect(screen.getByText('0.00% vs market')).toBeInTheDocument();
    expect(screen.getByText('+0.50% effective')).toBeInTheDocument();
    expect(screen.getByText('+0.38% vs market')).toBeInTheDocument();
    expect(screen.getByText('+0.88% effective')).toBeInTheDocument();
    expect(screen.getByText('Match')).toBeInTheDocument();
  });

  it('loads an existing watch into the form and submits the edit', async () => {
    const status = decodeDealWatchStatus(dealWatchPayload());
    const actions = controls();
    render(
      <ToastProvider>
        <DealWatchHavenoSection
          watches={status.havenoWatches}
          activity={status.havenoActivity}
          disabled={false}
          controls={actions}
        />
      </ToastProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    const threshold = screen.getByLabelText('Max % above market');
    expect(threshold).toHaveValue(-2);
    expect(screen.getByLabelText('Fee %')).toHaveValue(0.5);
    fireEvent.change(threshold, { target: { value: '-4.25' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));

    await waitFor(() =>
      expect(actions.editHavenoWatch).toHaveBeenCalledWith(21, {
        thresholdPct: '-4.25',
        feePct: '0.50',
        paymentMethod: 'ZELLE',
        currency: 'USD',
        minXmr: '',
        maxXmr: '10',
        title: '',
      }),
    );
  });

  it('submits a negative threshold and normalizes an empty fee to zero', async () => {
    const actions = controls();
    render(
      <ToastProvider>
        <DealWatchHavenoSection watches={[]} activity={null} disabled={false} controls={actions} />
      </ToastProvider>,
    );

    fireEvent.change(screen.getByLabelText('Max % above market'), {
      target: { value: '-1.25' },
    });
    fireEvent.change(screen.getByLabelText('Fee %'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add XMR watch' }));

    await waitFor(() =>
      expect(actions.addHavenoWatch).toHaveBeenCalledWith({
        thresholdPct: '-1.25',
        feePct: '0',
        paymentMethod: 'ZELLE',
        currency: 'USD',
        minXmr: '',
        maxXmr: '',
        title: '',
      }),
    );
  });
});
