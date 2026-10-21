import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { App } from './App';
import { ToastProvider } from './components/ToastProvider';
import { paneIds } from './dashboard/paneLinks';

describe('Van Dashboard React composition', () => {
  afterEach(() => vi.unstubAllGlobals());

  it.each(paneIds)('opens exactly one pane for #%s on arrival', (id) => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise<Response>(() => undefined)),
    );
    HTMLDialogElement.prototype.showModal = function () {
      this.setAttribute('open', '');
    };
    HTMLDialogElement.prototype.close = function () {
      this.removeAttribute('open');
    };
    window.history.replaceState(null, '', `/#${id}`);
    const view = render(
      <ToastProvider>
        <App />
      </ToastProvider>,
    );
    expect(screen.getAllByRole('dialog')).toHaveLength(1);
    view.unmount();
  });

  it('renders every tile while data is loading', () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise<Response>(() => undefined)),
    );

    const view = render(
      <ToastProvider>
        <App />
      </ToastProvider>,
    );

    // A tile that throws while its data is loading takes the whole dashboard down.
    expect(screen.getAllByRole('heading').length).toBeGreaterThan(10);

    view.unmount();
  });
});
