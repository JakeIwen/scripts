import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { getJson } from '../../api/client';
import { ToastProvider } from '../../components/ToastProvider';
import type { DealWatchControls } from './controls';
import { decodeDealWatchStatus } from './decoders';
import { DealWatchSheet } from './DealWatchSheet';
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

  it('rejects unsafe links', () => {
    const unsafe = dealWatchPayload();
    const unsafeItems = unsafe.items as Record<string, unknown>[];
    unsafeItems[0]!.url = 'javascript:alert(1)';
    expect(() => decodeDealWatchStatus(unsafe)).toThrow(
      'price checks.items[0].url must be a safe http or https URL',
    );
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

  it('opens only safe external links and wires the check and refresh controls', () => {
    const dealResource = resource();
    const actions = controls();
    render(
      <ToastProvider>
        <DealWatchSheet open onClose={vi.fn()} resource={dealResource} controls={actions} />
      </ToastProvider>,
    );

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
