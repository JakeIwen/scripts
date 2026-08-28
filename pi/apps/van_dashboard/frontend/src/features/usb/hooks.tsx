import { createContext, useContext, useEffect, useMemo, useState } from 'react';

import { useToast } from '../../components/ToastProvider';
import { usePollingResource, type PollingState } from '../../hooks/usePollingResource';
import { fetchUsbStatus } from './api';
import { useUsbControls, type UsbControls } from './controls';
import type { UsbStatus } from './types';

export const USB_TILE_POLL_INTERVAL_MS = 30_000;
export const USB_SHEET_POLL_INTERVAL_MS = 2_000;

export function useUsbStatus(sheetOpen: boolean): PollingState<UsbStatus> {
  return usePollingResource({
    load: fetchUsbStatus,
    intervalMs: sheetOpen ? USB_SHEET_POLL_INTERVAL_MS : USB_TILE_POLL_INTERVAL_MS,
  });
}

interface UsbStatusContextValue {
  resource: PollingState<UsbStatus>;
  controls: UsbControls;
  sheetOpen: boolean;
  setSheetOpen: (open: boolean) => void;
}

const UsbStatusContext = createContext<UsbStatusContextValue | null>(null);

/**
 * Share one USB snapshot between the USB tile and storage disk explanations.
 * The provider also owns the one detail-polling flag, avoiding duplicate GETs.
 */
export function UsbStatusProvider({ children }: { children: React.ReactNode }) {
  const [sheetOpen, setSheetOpen] = useState(false);
  const resource = useUsbStatus(sheetOpen);
  const { showToast } = useToast();
  const controls = useUsbControls(resource.refresh, showToast);
  useEffect(() => controls.reconcile(resource.data), [controls.reconcile, resource.data]);
  const value = useMemo(
    () => ({ resource, controls, sheetOpen, setSheetOpen }),
    [controls, resource, sheetOpen],
  );

  return <UsbStatusContext.Provider value={value}>{children}</UsbStatusContext.Provider>;
}

export function useSharedUsbStatus(): UsbStatusContextValue {
  const value = useContext(UsbStatusContext);
  if (!value) {
    throw new Error('USB and storage features must be rendered inside UsbStatusProvider');
  }
  return value;
}
