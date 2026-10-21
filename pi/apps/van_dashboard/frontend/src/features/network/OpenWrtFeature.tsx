import { usePaneLink } from '../../dashboard/paneLinks';

import { useSpeedtestControl } from './controls';
import { useOpenWrtResources, useWanThroughput } from './hooks';
import { OpenWrtSheet } from './OpenWrtSheet';
import { OpenWrtTile } from './OpenWrtTile';
import { presentWanThroughput } from './presentation';

export function OpenWrtFeature() {
  const [sheetOpen, setSheetOpen] = usePaneLink('openwrt');
  const { connectivity, speedtest, clients } = useOpenWrtResources(sheetOpen);
  const speedtestControl = useSpeedtestControl(speedtest);
  const throughput = presentWanThroughput(
    connectivity.data,
    useWanThroughput(connectivity.data?.router.wanTraffic),
  );

  return (
    <>
      <OpenWrtTile
        connectivity={connectivity.data}
        connectivityError={connectivity.error}
        connectivityRefreshing={connectivity.refreshing}
        throughput={throughput}
        speedtest={speedtest.data}
        speedtestError={speedtest.error}
        speedtestStarting={speedtestControl.busy}
        onStartSpeedtest={speedtestControl.start}
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
