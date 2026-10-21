import { usePaneLink } from '../../dashboard/paneLinks';

import { useVonstarController } from './hooks';
import { VonstarSheet } from './VonstarSheet';
import { VonstarTile } from './VonstarTile';

export function VonstarFeature() {
  const [sheetOpen, setSheetOpen] = usePaneLink('vonstar');
  const controller = useVonstarController();
  return (
    <>
      <VonstarTile controller={controller} onOpen={() => setSheetOpen(true)} />
      <VonstarSheet open={sheetOpen} onClose={() => setSheetOpen(false)} controller={controller} />
    </>
  );
}
