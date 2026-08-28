import { useEffect, useState } from 'react';

import { useToast } from '../../components/ToastProvider';
import { BackupsSheet } from './BackupsSheet';
import { BackupsTile } from './BackupsTile';
import { useBackupControls } from './controls';
import { useBackupStatus } from './hooks';

export function BackupsFeature() {
  const [sheetOpen, setSheetOpen] = useState(false);
  const resource = useBackupStatus(sheetOpen);
  const { showToast } = useToast();
  const controls = useBackupControls(resource.refresh, showToast);
  useEffect(() => controls.reconcile(resource.data), [controls.reconcile, resource.data]);
  return (
    <>
      <BackupsTile resource={resource} onOpen={() => setSheetOpen(true)} />
      <BackupsSheet
        open={sheetOpen}
        onClose={() => setSheetOpen(false)}
        resource={resource}
        controls={controls}
      />
    </>
  );
}
