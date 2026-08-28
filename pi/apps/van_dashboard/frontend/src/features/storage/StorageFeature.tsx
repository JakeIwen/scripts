import { useEffect, useState } from 'react';

import { useToast } from '../../components/ToastProvider';
import { useSharedUsbStatus } from '../usb/hooks';
import { useStorageControls } from './controls';
import { useStorageResources } from './hooks';
import { StorageSheet } from './StorageSheet';
import { StorageTile } from './StorageTile';

export function StorageFeature() {
  const [sheetOpen, setSheetOpen] = useState(false);
  const resources = useStorageResources(sheetOpen);
  const { showToast } = useToast();
  const controls = useStorageControls(resources.policy.refresh, resources.disks.refresh, showToast);
  const { resource: usbResource, controls: usbControls } = useSharedUsbStatus();
  useEffect(
    () => controls.reconcileDiskStatus(resources.disks.data),
    [controls.reconcileDiskStatus, resources.disks.data],
  );
  return (
    <>
      <StorageTile resources={resources} onOpen={() => setSheetOpen(true)} />
      <StorageSheet
        open={sheetOpen}
        onClose={() => setSheetOpen(false)}
        resources={resources}
        usbResource={usbResource}
        controls={controls}
        usbControls={usbControls}
      />
    </>
  );
}
