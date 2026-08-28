import { useSharedUsbStatus } from './hooks';
import { UsbSheet } from './UsbSheet';
import { UsbTile } from './UsbTile';

export function UsbFeature() {
  const { resource, controls, sheetOpen, setSheetOpen } = useSharedUsbStatus();
  return (
    <>
      <UsbTile resource={resource} onOpen={() => setSheetOpen(true)} />
      <UsbSheet
        open={sheetOpen}
        onClose={() => setSheetOpen(false)}
        resource={resource}
        controls={controls}
      />
    </>
  );
}
