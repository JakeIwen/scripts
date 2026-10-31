import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { postForm } from '../../api/client';
import { ToastProvider } from '../../components/ToastProvider';
import { useDealWatchControls } from './controls';
import { decodeDealWatchStatus } from './decoders';
import { DealWatchSearchSection } from './DealWatchSearchSection';
import { dealWatchPayload } from './testFixtures';
import type { DealWatchStatus } from './types';

vi.mock('../../api/client', () => ({ getJson: vi.fn(), postForm: vi.fn() }));
afterEach(cleanup);

function SearchView({
  data,
  refresh,
}: {
  data: DealWatchStatus;
  refresh: () => Promise<DealWatchStatus>;
}) {
  const controls = useDealWatchControls({
    data,
    refresh,
    error: null,
    initialLoading: false,
    refreshing: false,
    lastUpdatedAt: 1,
  });
  return <DealWatchSearchSection searches={data.searches} controls={controls} />;
}

function setup() {
  const data = decodeDealWatchStatus(dealWatchPayload());
  const refresh = vi.fn().mockResolvedValue(data);
  const renderView = (status = data) => (
    <ToastProvider>
      <SearchView data={status} refresh={refresh} />
    </ToastProvider>
  );
  const view = render(renderView());
  fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
  const form = screen.getByRole('form', { name: 'Edit Impact drivers' });
  return { data, refresh, view, renderView, form };
}

describe('saved search editor', () => {
  it('prefills, retains an unsaved draft across polling, and edits the existing ID', async () => {
    vi.mocked(postForm).mockResolvedValue({ ok: true, message: 'Updated Shop tools' });
    const { data, refresh, view, renderView, form } = setup();
    expect(within(form).getByLabelText(/Title/)).toHaveValue('Impact drivers');
    expect(within(form).getByLabelText('eBay search URL')).toHaveValue(data.searches[0]!.url);
    fireEvent.change(within(form).getByLabelText(/Title/), { target: { value: ' Shop tools ' } });
    fireEvent.change(within(form).getByLabelText('eBay search URL'), {
      target: { value: 'https://www.ebay.com/sch/i.html?_nkw=tools&_udhi=200' },
    });
    view.rerender(renderView(decodeDealWatchStatus(dealWatchPayload())));
    expect(within(form).getByLabelText(/Title/)).toHaveValue(' Shop tools ');
    fireEvent.click(within(form).getByRole('button', { name: 'Save changes' }));
    await waitFor(() =>
      expect(screen.queryByRole('form', { name: 'Edit Impact drivers' })).not.toBeInTheDocument(),
    );
    expect(postForm).toHaveBeenCalledExactlyOnceWith('/api/price-checks/searches/edit', {
      id: '12',
      parser: 'ebay',
      title: 'Shop tools',
      url: 'https://www.ebay.com/sch/i.html?_nkw=tools&_udhi=200',
    });
    expect(refresh).toHaveBeenCalledOnce();
    expect(screen.getByText('Updated Shop tools')).toBeVisible();
    expect(screen.getByText('Milwaukee impact driver kit')).toBeVisible();
  });

  it('retains failed edits with an error message, and cancel makes no further request', async () => {
    vi.mocked(postForm).mockRejectedValue(new Error('search URL is already configured'));
    const { refresh, form } = setup();
    fireEvent.change(within(form).getByLabelText(/Title/), {
      target: { value: 'Keep this draft' },
    });
    fireEvent.click(within(form).getByRole('button', { name: 'Save changes' }));
    await screen.findByText('search URL is already configured');
    expect(within(form).getByLabelText(/Title/)).toHaveValue('Keep this draft');
    await waitFor(() => expect(within(form).getByRole('button', { name: 'Cancel' })).toBeEnabled());
    fireEvent.click(within(form).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('form', { name: 'Edit Impact drivers' })).not.toBeInTheDocument();
    expect(postForm).toHaveBeenCalledOnce();
    expect(refresh).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    expect(
      within(screen.getByRole('form', { name: 'Edit Impact drivers' })).getByLabelText(/Title/),
    ).toHaveValue('Impact drivers');
  });
});
