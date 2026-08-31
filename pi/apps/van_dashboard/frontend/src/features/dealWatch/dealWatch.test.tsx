import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { getJson } from '../../api/client';
import { ToastProvider } from '../../components/ToastProvider';
import type { DealWatchControls } from './controls';
import { fetchDealWatchStatus } from './api';
import { decodeDealWatchStatus } from './decoders';
import { DealWatchSheet } from './DealWatchSheet';
import { DealWatchTile } from './DealWatchTile';
import { DEAL_WATCH_POLL_INTERVAL_MS, useDealWatchStatus } from './hooks';
import { dealWatchPayload } from './testFixtures';

vi.mock('../../api/client', () => ({ getJson: vi.fn(), postForm: vi.fn() }));

beforeAll(() => {
  if (!HTMLDialogElement.prototype.showModal) {
    HTMLDialogElement.prototype.showModal = function showModal() {
      this.setAttribute('open', '');
    };
  }
  if (!HTMLDialogElement.prototype.close) {
    HTMLDialogElement.prototype.close = function close() {
      this.removeAttribute('open');
      this.dispatchEvent(new Event('close'));
    };
  }
});

beforeEach(() => {
  vi.mocked(getJson).mockResolvedValue(dealWatchPayload());
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('Deal Watch decoder', () => {
  it('decodes listing summaries, saved results, and the cron schedule', () => {
    const status = decodeDealWatchStatus(dealWatchPayload());

    expect(status.listingSummary).toEqual({
      count: 2,
      checked: 2,
      belowThreshold: 1,
      errors: 1,
    });
    expect(status.listings[0]).toMatchObject({
      displayTitle: 'Protein shakes',
      lastPrice: '49.95',
      thresholdCents: 5_500,
      belowThreshold: true,
      notificationsMuted: true,
    });
    expect(status.searches[0]?.results[0]).toMatchObject({
      itemId: '123456789012',
      title: 'Milwaukee impact driver kit',
      imageUrl: '//i.ebayimg.com/images/g/example.jpg',
      dismissed: false,
    });
    expect(status.schedule).toEqual({
      expression: '0 10,15,20 * * *',
      description: 'At minute 0 past hours 10, 15 and 20',
      error: null,
      errorCode: null,
    });
  });

  it('rejects unsafe links and price/count contradictions', () => {
    const unsafe = dealWatchPayload();
    const unsafeItems = unsafe.items as Record<string, unknown>[];
    unsafeItems[0]!.url = 'javascript:alert(1)';
    expect(() => decodeDealWatchStatus(unsafe)).toThrow(
      'price checks.items[0].url must be a safe http or https URL',
    );

    const wrongPrice = dealWatchPayload();
    const wrongPriceItems = wrongPrice.items as Record<string, unknown>[];
    wrongPriceItems[0]!.last_price_cents = 4_994;
    expect(() => decodeDealWatchStatus(wrongPrice)).toThrow(
      'price checks.items[0].last_price disagrees',
    );

    const wrongSummary = dealWatchPayload();
    const summary = wrongSummary.summary as Record<string, unknown>;
    summary.errors = 0;
    expect(() => decodeDealWatchStatus(wrongSummary)).toThrow(
      'price checks.summary does not match price checks.items',
    );
  });

  it('keeps schedule failures explicit and rejects inconsistent schedule state', () => {
    const unavailable = dealWatchPayload();
    unavailable.schedule = {
      expression: '',
      description: '',
      error: 'could not read price-check schedule',
      error_code: 'parse',
    };
    expect(decodeDealWatchStatus(unavailable).schedule).toMatchObject({
      errorCode: 'parse',
      error: 'could not read price-check schedule',
    });

    const contradictory = dealWatchPayload();
    const schedule = contradictory.schedule as Record<string, unknown>;
    schedule.error = 'parser failed';
    expect(() => decodeDealWatchStatus(contradictory)).toThrow(
      'failed price-check schedule has inconsistent error details',
    );
  });
});

describe('Deal Watch read API and polling', () => {
  it('loads only GET /api/price-checks and decodes the response', async () => {
    const signal = new AbortController().signal;
    const status = await fetchDealWatchStatus(signal);

    expect(getJson).toHaveBeenCalledOnce();
    expect(getJson).toHaveBeenCalledWith('/api/price-checks', signal);
    expect(status.searchSummary.results).toBe(1);
  });

  it('polls every 30 seconds only while visible and refreshes when the sheet opens', async () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(({ open }) => useDealWatchStatus(open), {
      initialProps: { open: false },
    });
    await act(async () => Promise.resolve());

    expect(result.current.data?.listingSummary.count).toBe(2);
    expect(getJson).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(DEAL_WATCH_POLL_INTERVAL_MS);
    });
    expect(getJson).toHaveBeenCalledTimes(2);

    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(DEAL_WATCH_POLL_INTERVAL_MS);
    });
    expect(getJson).toHaveBeenCalledTimes(2);

    hidden.mockReturnValue(false);
    rerender({ open: true });
    await act(async () => Promise.resolve());
    expect(getJson).toHaveBeenCalledTimes(3);
  });
});

describe('Deal Watch views', () => {
  function resource() {
    const data = decodeDealWatchStatus(dealWatchPayload());
    return {
      data,
      error: null,
      initialLoading: false,
      refreshing: false,
      lastUpdatedAt: Date.now(),
      refresh: vi.fn().mockResolvedValue(data),
    };
  }

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
      saveSchedule: vi.fn().mockResolvedValue(true),
    };
  }

  it('summarizes the latest price, errors, and schedule in the tile', () => {
    const onOpen = vi.fn();
    render(<DealWatchTile resource={resource()} onOpen={onOpen} />);

    expect(screen.getByText('2 listings · 1 queries · 1 errors')).toBeInTheDocument();
    expect(screen.getByText('$49.95 · Protein shakes')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Open Deal Watch details' }));
    expect(onOpen).toHaveBeenCalledOnce();
  });

  it('shows safe external links, schedule, results, and mutation controls', () => {
    const dealResource = resource();
    const actions = controls();
    render(
      <ToastProvider>
        <DealWatchSheet open onClose={vi.fn()} resource={dealResource} controls={actions} />
      </ToastProvider>,
    );

    expect(screen.getByLabelText('Cron schedule')).toHaveValue('0 10,15,20 * * *');
    expect(screen.getByText('At minute 0 past hours 10, 15 and 20')).toBeInTheDocument();
    expect(screen.getByText('Amazon product markup changed')).toBeInTheDocument();
    expect(screen.getByText('$119.00 · Free shipping')).toBeInTheDocument();

    const links = screen.getAllByRole('link');
    expect(links).toHaveLength(4);
    for (const link of links) {
      expect(link).toHaveAttribute('target', '_blank');
      expect(link).toHaveAttribute('rel', 'noopener noreferrer');
      expect(link.getAttribute('href')).toMatch(/^https:\/\//);
    }

    fireEvent.click(screen.getByRole('button', { name: 'Check all now' }));
    expect(actions.checkListings).toHaveBeenCalledWith('all');
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    expect(dealResource.refresh).toHaveBeenCalledOnce();
  });
});
