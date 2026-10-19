import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { DealWatchHavenoActivity } from './DealWatchHavenoActivity';
import { decodeDealWatchHavenoActivity } from './havenoActivityDecoders';
import { dealWatchPayload } from './testFixtures';

function activityPayload(): Record<string, unknown> {
  return dealWatchPayload().haveno_activity as Record<string, unknown>;
}

function emptyActivityPayload(lastError: string | null = null): Record<string, unknown> {
  return {
    network: 'reto',
    currency: 'USD',
    payment_method: 'ZELLE',
    tracking_since: null,
    last_snapshot_at: null,
    last_error: lastError,
    current_offers: 0,
    windows: [
      {
        days: 7,
        listed: 0,
        taken: 0,
        taken_high: 0,
        withdrawn: 0,
        pending: 0,
        taken_xmr: '0',
        median_hours_to_take: null,
        market_trades: 0,
        market_trades_xmr: '0',
      },
      {
        days: 30,
        listed: 0,
        taken: 0,
        taken_high: 0,
        withdrawn: 0,
        pending: 0,
        taken_xmr: '0',
        median_hours_to_take: null,
        market_trades: 0,
        market_trades_xmr: '0',
      },
    ],
    recent: [],
  };
}

function decodeActivity(activity: Record<string, unknown>) {
  return decodeDealWatchHavenoActivity({ haveno_activity: activity });
}

function expandActivity() {
  fireEvent.click(screen.getByText('Zelle offer activity').closest('summary')!);
}

afterEach(cleanup);

describe('Haveno activity decoder', () => {
  it('returns null when the deploy-tolerant key is absent', () => {
    expect(decodeDealWatchHavenoActivity({})).toBeNull();
  });

  it('decodes the activity windows and recent offer evidence', () => {
    const decoded = decodeActivity(activityPayload());
    expect(decoded).toMatchObject({
      network: 'reto',
      currency: 'USD',
      paymentMethod: 'ZELLE',
      currentOffers: 15,
      windows: [
        { days: 7, taken: 3, takenHigh: 2, medianHoursToTake: '6.5' },
        { days: 30, withdrawn: 14, marketTradesXmr: '31.75' },
      ],
    });
    expect(decoded?.recent[0]).toMatchObject({
      offerId: 'taken-offer',
      outcome: 'taken',
      confidence: 'high',
      tradePrice: '566.10',
      tradeXmr: '2.001',
    });
  });

  it('rejects malformed activity instead of hiding it', () => {
    const badOutcome = activityPayload();
    (badOutcome.recent as Record<string, unknown>[])[0]!.outcome = 'sold';
    expect(() => decodeActivity(badOutcome)).toThrow('outcome');

    const badWindow = activityPayload();
    (badWindow.windows as Record<string, unknown>[])[0]!.taken_high = 4;
    expect(() => decodeActivity(badWindow)).toThrow('taken_high');

    const incompleteTrade = activityPayload();
    (incompleteTrade.recent as Record<string, unknown>[])[0]!.trade_price = null;
    expect(() => decodeActivity(incompleteTrade)).toThrow('trade details');
  });
});

describe('Haveno activity view', () => {
  it('is collapsed by default and reveals activity details on expansion', () => {
    const activity = decodeActivity(activityPayload());
    const { container } = render(<DealWatchHavenoActivity activity={activity} />);
    const disclosure = container.querySelector('details');

    expect(disclosure).not.toHaveAttribute('open');
    expect(screen.getByText('3 taken · 4 withdrawn (7d)')).toBeVisible();
    expect(screen.getByText('Recent vanished offers')).not.toBeVisible();

    expandActivity();

    expect(disclosure).toHaveAttribute('open');
    expect(screen.getByText('Recent vanished offers')).toBeVisible();
    expect(container.querySelector('.deal-watch-activity-offer .status-pill')).toHaveTextContent(
      'Taken',
    );
    expect(screen.getByText('Matched trade $566.10 · 2.001 XMR')).toBeInTheDocument();
  });

  it('shows the not-collected and empty states', () => {
    const activity = decodeActivity(emptyActivityPayload());
    render(<DealWatchHavenoActivity activity={activity} />);

    expect(screen.getByText('Not collected yet')).toBeInTheDocument();
    expandActivity();
    expect(screen.getByText('No vanished offers recorded.')).toBeVisible();
    expect(screen.getByText('Never')).toBeVisible();
  });

  it('shows a snapshot error without discarding prior activity', () => {
    const payload = activityPayload();
    payload.last_error = 'Trades feed unavailable';
    const activity = decodeActivity(payload);
    render(<DealWatchHavenoActivity activity={activity} />);

    expandActivity();
    expect(screen.getByText('Trades feed unavailable')).toBeVisible();
    expect(screen.getByText('3 taken · 4 withdrawn (7d)')).toBeVisible();
  });
});
