import { useState } from 'react';

import { useSpeedtestControl } from './controls';
import { useOpenWrtResources } from './hooks';
import { OpenWrtSheet } from './OpenWrtSheet';
import { OpenWrtTile } from './OpenWrtTile';

export function OpenWrtFeature() {
  const [sheetOpen, setSheetOpen] = useState(false);
  const { connectivity, speedtest, clients } = useOpenWrtResources(sheetOpen);
  const speedtestControl = useSpeedtestControl(speedtest);

  return (
    <>
      <OpenWrtTile
        connectivity={connectivity.data}
        connectivityError={connectivity.error}
        connectivityRefreshing={connectivity.refreshing}
        speedtest={speedtest.data}
        speedtestError={speedtest.error}
        onOpen={() => setSheetOpen(true)}
      />
      <OpenWrtSheet
        open={sheetOpen}
        onClose={() => setSheetOpen(false)}
        connectivity={connectivity.data}
        connectivityError={connectivity.error}
        clients={clients.data}
        clientsError={clients.error}
        clientsInitialLoading={clients.initialLoading}
        clientsRefreshing={clients.refreshing}
        onRefreshClients={clients.refresh}
        speedtest={speedtest.data}
        speedtestError={speedtest.error}
        speedtestStarting={speedtestControl.busy}
        onStartSpeedtest={speedtestControl.start}
      />
    </>
  );
}
