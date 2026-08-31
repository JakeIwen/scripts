interface DashboardHeaderProps {
  uptime?: string;
  systemControls?: React.ReactNode;
}

export function DashboardHeader({ uptime, systemControls }: DashboardHeaderProps) {
  return (
    <header className="dashboard-header">
      <div>
        <p className="dashboard-header__eyebrow">vanpi controls</p>
        <h1>Van Dashboard</h1>
      </div>
      <div className="dashboard-header__actions">
        <div className="dashboard-header__system">
          {systemControls}
          {uptime && <p className="dashboard-header__uptime">{uptime}</p>}
        </div>
        <span className="dashboard-header__van" aria-hidden="true">
          🚐
        </span>
      </div>
    </header>
  );
}
