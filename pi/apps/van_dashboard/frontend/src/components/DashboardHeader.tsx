interface DashboardHeaderProps {
  editing: boolean;
  onEditingChange: (editing: boolean) => void;
  uptime?: string;
  systemControls?: React.ReactNode;
}

function legacyDashboardUrl(): string {
  const url = new URL(window.location.href);
  url.port = '8788';
  url.pathname = '/';
  url.search = '';
  url.hash = '';
  return url.toString();
}

export function DashboardHeader({
  editing,
  onEditingChange,
  uptime,
  systemControls,
}: DashboardHeaderProps) {
  return (
    <header className="dashboard-header">
      <div>
        <p className="dashboard-header__eyebrow">vanpi controls · React preview</p>
        <h1>Van Dashboard</h1>
        {uptime && <p className="dashboard-header__uptime">{uptime}</p>}
      </div>
      <div className="dashboard-header__actions">
        {systemControls}
        <a className="secondary-button" href={legacyDashboardUrl()}>
          Legacy UI
        </a>
        <button
          className={`icon-button ${editing ? 'icon-button--active' : ''}`}
          type="button"
          aria-pressed={editing}
          onClick={() => onEditingChange(!editing)}
          title={editing ? 'Finish arranging tiles' : 'Arrange tiles'}
        >
          {editing ? '✓' : '✎'}
          <span className="visually-hidden">
            {editing ? 'Finish arranging tiles' : 'Arrange tiles'}
          </span>
        </button>
      </div>
    </header>
  );
}
