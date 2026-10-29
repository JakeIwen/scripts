import { useCallback, useSyncExternalStore } from 'react';

/** Stable public fragments for expandable dashboard panes. */
export const paneIds = [
  'hosted',
  'vonstar',
  'sonos',
  'storage',
  'system-monitor',
  'compute-worker',
  'usb-devices',
  'backups',
  'media-organizer',
  'ignition-monitor',
  'price-checks',
  'lighting',
  'ubnt-wifi',
  'network-history',
  'openwrt',
] as const;

type PaneId = (typeof paneIds)[number];

function readPane() {
  return window.location.hash.slice(1).split('?')[0];
}

function subscribe(onChange: () => void) {
  window.addEventListener('hashchange', onChange);
  window.addEventListener('popstate', onChange);
  return () => {
    window.removeEventListener('hashchange', onChange);
    window.removeEventListener('popstate', onChange);
  };
}

export function writePaneHash(hash: string, replace = false) {
  const url = new URL(window.location.href);
  url.hash = hash;
  if (url.href === window.location.href) return;
  // History API avoids scrolling the tile behind the modal into view.
  window.history[replace ? 'replaceState' : 'pushState'](window.history.state, '', url);
  window.dispatchEvent(new HashChangeEvent('hashchange'));
}

export function usePaneLink(id: PaneId): readonly [boolean, (open: boolean) => void] {
  const activePane = useSyncExternalStore(subscribe, readPane);
  const setOpen = useCallback(
    (open: boolean) => {
      if (open) {
        if (readPane() !== id) writePaneHash(id);
      } else if (readPane() === id) {
        // A delayed close event from the previous pane must not clear the new pane.
        writePaneHash('');
      }
    },
    [id],
  );
  return [activePane === id, setOpen];
}
