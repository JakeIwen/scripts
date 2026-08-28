import { useState } from 'react';

import { DashboardHeader } from './components/DashboardHeader';
import { ServiceLinkTile } from './components/Tile';
import { DashboardGrid, type DashboardTile } from './dashboard/DashboardGrid';
import { BackupsFeature } from './features/backups';
import { ComputeFeature } from './features/compute';
import { DealWatchFeature } from './features/dealWatch';
import { IgnitionFeature } from './features/ignition';
import { LightingFeature } from './features/lighting';
import { OpenWrtFeature } from './features/network';
import { SonosFeature } from './features/sonos';
import { DashboardStatusTile, formatDashboardUptime, useDashboardStatus } from './features/status';
import { StorageFeature } from './features/storage';
import { SystemControls } from './features/systemControls';
import { SystemHealthFeature } from './features/systemHealth';
import { TelemetryTile } from './features/telemetry';
import { UbntFeature } from './features/ubnt';
import { UsbFeature, UsbStatusProvider } from './features/usb';

export function App() {
  const [editing, setEditing] = useState(false);
  const dashboardStatus = useDashboardStatus();

  const tiles: DashboardTile[] = [
    {
      id: 'cop',
      content: <DashboardStatusTile resource={dashboardStatus} />,
    },
    {
      id: 'books',
      content: (
        <ServiceLinkTile
          icon="📖"
          title="Audiobooks"
          detail="Open the Sonos audiobook library"
          port={8787}
        />
      ),
    },
    {
      id: 'video-library',
      content: (
        <ServiceLinkTile
          icon="🎬"
          title="Movies & TV"
          detail="Browse, play, and continue movies and shows"
          port={8789}
        />
      ),
    },
    {
      id: 'telemetry',
      content: <TelemetryTile />,
    },
    {
      id: 'sonos-card',
      content: <SonosFeature />,
    },
    {
      id: 'storage',
      content: <StorageFeature />,
    },
    {
      id: 'system-monitor',
      content: <SystemHealthFeature />,
    },
    {
      id: 'compute-worker',
      content: <ComputeFeature />,
    },
    {
      id: 'usb-devices',
      content: <UsbFeature />,
    },
    {
      id: 'backups',
      content: <BackupsFeature />,
    },
    {
      id: 'ignition-monitor',
      content: <IgnitionFeature />,
    },
    {
      id: 'price-checks',
      content: <DealWatchFeature />,
    },
    {
      id: 'lighting-card',
      content: <LightingFeature />,
    },
    {
      id: 'ubnt-wifi',
      content: <UbntFeature dashboardStatus={dashboardStatus} />,
    },
    {
      id: 'openwrt-card',
      content: <OpenWrtFeature />,
    },
  ];

  return (
    <div className="dashboard-app">
      <DashboardHeader
        editing={editing}
        onEditingChange={setEditing}
        uptime={
          dashboardStatus.data
            ? formatDashboardUptime(dashboardStatus.data.systemUptime.seconds)
            : undefined
        }
        systemControls={<SystemControls disabled={editing} />}
      />
      <UsbStatusProvider>
        <DashboardGrid tiles={tiles} editing={editing} />
      </UsbStatusProvider>
    </div>
  );
}
