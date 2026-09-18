import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { ToastProvider, useToast } from './ToastProvider';

function ToastHarness() {
  const { showToast } = useToast();
  return (
    <>
      <dialog open aria-label="Focused controls">
        <p>Focused sheet</p>
      </dialog>
      <button type="button" onClick={() => showToast('Operation complete')}>
        Show notification
      </button>
    </>
  );
}

afterEach(cleanup);

describe('ToastProvider', () => {
  it('portals notifications into the active modal top layer', async () => {
    render(
      <ToastProvider>
        <ToastHarness />
      </ToastProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Show notification' }));
    const dialog = screen.getByRole('dialog', { name: 'Focused controls' });

    await waitFor(() =>
      expect(within(dialog).getByRole('status')).toHaveTextContent('Operation complete'),
    );
    expect(within(document.body).getAllByRole('status')).toHaveLength(1);
  });
});
