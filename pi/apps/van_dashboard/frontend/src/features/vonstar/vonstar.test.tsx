import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { getJson, postForm } from '../../api/client';
import { ToastProvider } from '../../components/ToastProvider';
import { decodeVonstarAccessMutation, decodeVonstarStatus } from './decoders';
import { VonstarFeature } from './VonstarFeature';
import { VonstarTile } from './VonstarTile';
import type { VonstarController } from './types';

vi.mock('../../api/client', () => ({ getJson: vi.fn(), postForm: vi.fn() }));

function accessState() {
  const unmapped = {
    locked: null,
    ajar: null,
    reported_closed: null,
    physical_state_observable: null,
    lock_quality: 'unmapped_individual_door',
    ajar_quality: 'unmapped_individual_door',
  };
  return {
    observed_at: '2026-08-28T01:52:00+00:00',
    complete: false,
    lock_domains: {
      state: 'locked',
      front_locked: true,
      cargo_locked: true,
      quality: 'verified',
    },
    doors: {
      driver: {
        ...unmapped,
        ajar: false,
        ajar_quality: 'candidate_one_controlled_trial',
      },
      passenger: { ...unmapped },
      sliding: {
        ...unmapped,
        reported_closed: true,
        physical_state_observable: false,
        ajar_quality: 'hardware_bypass_forced_closed',
      },
      rear: { ...unmapped },
    },
    wake: { count: 1, restored_passive_after_sampling: true },
    observations: { raw: 'must not enter decoded UI state' },
    limitations: ['Individual passenger and rear ajar states are not mapped.'],
  };
}

function statusPayload() {
  return {
    ok: true,
    vonstar: {
      service: 'vonstar',
      available: true,
      mode: 'execute',
      busy: false,
      actions: {
        lock_all: { label: 'Lock All', validation: 'mapped_capture' },
        unlock_front: { label: 'Unlock Front', validation: 'live_verified' },
        unlock_cargo: { label: 'Unlock Cargo', validation: 'mapped_capture' },
      },
      cooldown_seconds: 3,
      last_result: null,
      error: null,
    },
  };
}

function actionPayload(action: 'lock_all' | 'unlock_front' | 'unlock_cargo') {
  const payload = statusPayload();
  return {
    ...payload,
    message:
      action === 'lock_all'
        ? 'Lock All completed'
        : action === 'unlock_front'
          ? 'Unlock Front completed'
          : 'Unlock Cargo completed',
    result: {
      action,
      label:
        action === 'lock_all'
          ? 'Lock All'
          : action === 'unlock_front'
            ? 'Unlock Front'
            : 'Unlock Cargo',
      ok: true,
    },
  };
}

function accessPayload() {
  return {
    ok: true,
    operation: 'access_state',
    access_state: accessState(),
    completed_at: '2026-08-28T01:52:01+00:00',
  };
}

function renderFeature() {
  return render(
    <ToastProvider>
      <VonstarFeature />
    </ToastProvider>,
  );
}

function readyController(): VonstarController {
  return {
    status: decodeVonstarStatus(statusPayload()),
    accessState: null,
    error: null,
    loading: false,
    refreshing: false,
    busy: false,
    lastAttempt: null,
    refresh: vi.fn().mockResolvedValue(null),
    perform: vi.fn().mockResolvedValue(true),
    checkAccessState: vi.fn().mockResolvedValue(true),
  };
}

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
  vi.clearAllMocks();
  vi.mocked(getJson).mockResolvedValue(statusPayload());
  vi.mocked(postForm).mockResolvedValue(actionPayload('unlock_front'));
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('vOnStar strict decoders', () => {
  it('accepts the exact catalog and drops raw observations from bounded UI state', () => {
    expect(decodeVonstarStatus(statusPayload())).toMatchObject({
      service: 'vonstar',
      mode: 'execute',
      available: true,
    });
    const result = decodeVonstarAccessMutation(accessPayload());
    expect(result.accessState?.doors.sliding.reportedClosed).toBe(true);
    expect(result.accessState).not.toHaveProperty('observations');
  });

  it('rejects catalog drift, contradictory verified locks, and oversized notes', () => {
    const badCatalog = statusPayload();
    delete (badCatalog.vonstar.actions as Record<string, unknown>).unlock_cargo;
    expect(() => decodeVonstarStatus(badCatalog)).toThrow(/exactly the three supported actions/);

    const badLock = accessPayload();
    badLock.access_state.lock_domains.front_locked = false;
    expect(() => decodeVonstarAccessMutation(badLock)).toThrow(/verified .* inconsistent/);

    const oversized = accessPayload();
    oversized.access_state.limitations = Array.from({ length: 33 }, () => 'bounded');
    expect(() => decodeVonstarAccessMutation(oversized)).toThrow(/at most 32/);
  });
});

describe('vOnStar controls', () => {
  it('renders plan-only, service-busy, and local working states', () => {
    const planOnly = readyController();
    if (!planOnly.status) throw new Error('missing vOnStar status fixture');
    planOnly.status = { ...planOnly.status, mode: 'plan_only' };
    const planView = render(<VonstarTile controller={planOnly} onOpen={vi.fn()} />);
    expect(screen.getByText('Plan only')).toBeVisible();
    expect(screen.getByText(/vehicle actions are disabled/i)).toBeVisible();
    for (const button of screen.getAllByRole('button', { name: /Lock|Unlock/ })) {
      expect(button).toBeDisabled();
    }
    planView.unmount();

    const busy = readyController();
    if (!busy.status) throw new Error('missing vOnStar status fixture');
    busy.status = { ...busy.status, busy: true };
    busy.lastAttempt = { label: 'Unlock Front', state: 'working' };
    render(<VonstarTile controller={busy} onOpen={vi.fn()} />);
    expect(screen.getByText('Busy')).toBeVisible();
    expect(screen.getByText(/another guarded vehicle operation/i)).toBeVisible();
    expect(screen.getByText('Last action').closest('[role="status"]')).toHaveTextContent(
      'Unlock Front · WORKING',
    );
  });

  it('does one initial GET, no automatic access check, and no polling', async () => {
    vi.useFakeTimers();
    renderFeature();
    await act(async () => Promise.resolve());
    expect(getJson).toHaveBeenCalledOnce();
    expect(getJson).toHaveBeenCalledWith('/api/vonstar', expect.any(AbortSignal));
    expect(postForm).not.toHaveBeenCalled();

    await act(async () => vi.advanceTimersByTimeAsync(60_000));
    expect(getJson).toHaveBeenCalledOnce();
    expect(postForm).not.toHaveBeenCalled();
    vi.useRealTimers();
  });

  it('confirms an exact action, never retries it, and refreshes status afterward', async () => {
    vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
    vi.mocked(postForm).mockResolvedValue(actionPayload('lock_all'));
    renderFeature();
    expect(await screen.findByText('Vehicle lock controls')).toBeVisible();

    const button = screen.getByRole('button', { name: 'Lock All' });
    fireEvent.click(button);
    expect(postForm).not.toHaveBeenCalled();
    fireEvent.click(button);

    await waitFor(() => expect(postForm).toHaveBeenCalledOnce());
    await waitFor(() => expect(getJson).toHaveBeenCalledTimes(2));
    expect(postForm).toHaveBeenCalledWith('/api/vonstar', { action: 'lock_all' });
    expect(window.confirm).toHaveBeenLastCalledWith(
      expect.stringContaining('capture-mapped but has not yet been independently replay-tested'),
    );
  });

  it('checks state only after confirmation and never calls access state on sheet open', async () => {
    vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
    renderFeature();
    expect(await screen.findByText('Vehicle lock controls')).toBeVisible();

    fireEvent.click(screen.getByRole('button', { name: 'Open vOnStar status' }));
    expect(postForm).not.toHaveBeenCalled();
    const check = screen.getByRole('button', { name: 'Check Status' });
    fireEvent.click(check);
    expect(postForm).not.toHaveBeenCalled();

    vi.mocked(postForm).mockResolvedValueOnce(accessPayload());
    fireEvent.click(check);
    await screen.findByText('All locked');
    expect(postForm).toHaveBeenCalledOnce();
    expect(postForm).toHaveBeenCalledWith('/api/vonstar/access-state');
  });

  it('blocks a second operation while one request is in flight', async () => {
    let release: ((value: unknown) => void) | undefined;
    vi.mocked(postForm).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderFeature();
    expect(await screen.findByText('Vehicle lock controls')).toBeVisible();

    fireEvent.click(screen.getByRole('button', { name: 'Unlock Front' }));
    fireEvent.click(screen.getByRole('button', { name: 'Unlock Cargo' }));
    expect(postForm).toHaveBeenCalledOnce();

    await act(async () => {
      release?.(actionPayload('unlock_front'));
    });
    await waitFor(() => expect(getJson).toHaveBeenCalledTimes(2));
    expect(postForm).toHaveBeenCalledOnce();
  });
});
