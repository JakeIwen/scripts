import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { paneIds, usePaneLink } from './paneLinks';

describe('pane links', () => {
  it.each(paneIds)('opens %s from a copied URL', (id) => {
    window.history.replaceState(null, '', `/?view=van#${id}`);
    const { result } = renderHook(() => usePaneLink(id));
    expect(result.current[0]).toBe(true);
    act(() => result.current[1](false));
    expect(window.location.hash).toBe('');
    expect(window.location.search).toBe('?view=van');
  });

  it('switches panes, ignores stale closes, and follows Back and Forward', async () => {
    const { result } = renderHook(() => ({
      storage: usePaneLink('storage'),
      usb: usePaneLink('usb-devices'),
    }));
    act(() => result.current.storage[1](true));
    expect(window.location.hash).toBe('#storage');
    act(() => result.current.usb[1](true));
    act(() => result.current.storage[1](false));
    expect(result.current.storage[0]).toBe(false);
    expect(result.current.usb[0]).toBe(true);
    act(() => window.history.back());
    await waitFor(() => expect(result.current.storage[0]).toBe(true));
    act(() => window.history.forward());
    await waitFor(() => expect(result.current.usb[0]).toBe(true));
  });

  it('responds to anchor navigation and leaves unknown fragments untouched', () => {
    const { result } = renderHook(() => usePaneLink('backups'));
    act(() => {
      window.location.hash = 'backups';
      window.dispatchEvent(new HashChangeEvent('hashchange'));
    });
    expect(result.current[0]).toBe(true);
    act(() => {
      window.location.hash = 'unknown';
      window.dispatchEvent(new HashChangeEvent('hashchange'));
    });
    expect(result.current[0]).toBe(false);
    act(() => result.current[1](false));
    expect(window.location.hash).toBe('#unknown');
  });
});
