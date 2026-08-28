import { useState } from 'react';

import { useComputeReport } from './hooks';
import { ComputeSheet } from './ComputeSheet';
import { ComputeTile } from './ComputeTile';
import type { ComputeRange } from './types';

export function ComputeFeature() {
  const [sheetOpen, setSheetOpen] = useState(false);
  const [rangeHours, setRangeHours] = useState<ComputeRange>(168);
  const resource = useComputeReport(rangeHours, sheetOpen);

  return (
    <>
      <ComputeTile
        report={resource.data}
        error={resource.error}
        refreshing={resource.refreshing}
        onOpen={() => setSheetOpen(true)}
      />
      <ComputeSheet
        open={sheetOpen}
        onClose={() => setSheetOpen(false)}
        rangeHours={rangeHours}
        onRangeChange={setRangeHours}
        resource={resource}
      />
    </>
  );
}
