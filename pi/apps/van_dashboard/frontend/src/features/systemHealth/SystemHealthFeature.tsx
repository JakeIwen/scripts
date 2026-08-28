import { useState } from 'react';

import { useToast } from '../../components/ToastProvider';
import { useCrashAnalysis } from './crashControls';
import { useCrashHistory } from './crashHooks';
import { useSystemHealthReport } from './hooks';
import { SystemHealthSheet } from './SystemHealthSheet';
import { SystemHealthTile } from './SystemHealthTile';
import type { SystemHealthRange } from './types';

export function SystemHealthFeature() {
  const [sheetOpen, setSheetOpen] = useState(false);
  const [rangeHours, setRangeHours] = useState<SystemHealthRange>(6);
  const resource = useSystemHealthReport(rangeHours, sheetOpen);
  const history = useCrashHistory(sheetOpen);
  const { showToast } = useToast();
  const crashAnalysis = useCrashAnalysis(history.refresh, showToast);

  return (
    <>
      <SystemHealthTile
        report={resource.data}
        error={resource.error}
        refreshing={resource.refreshing}
        onOpen={() => setSheetOpen(true)}
      />
      <SystemHealthSheet
        open={sheetOpen}
        onClose={() => setSheetOpen(false)}
        rangeHours={rangeHours}
        onRangeChange={setRangeHours}
        resource={resource}
        crashHistory={history}
        crashAnalysis={crashAnalysis}
      />
    </>
  );
}
