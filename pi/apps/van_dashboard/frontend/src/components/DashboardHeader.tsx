import { useState } from 'react';

import { ProjectDirectory } from '../features/projects/ProjectDirectory';

interface DashboardHeaderProps {
  uptime?: string;
  systemControls?: React.ReactNode;
}

export function DashboardHeader({ uptime, systemControls }: DashboardHeaderProps) {
  const [projectsOpen, setProjectsOpen] = useState(false);
  return (
    <>
      <header className="dashboard-header">
        <div>
          <p className="dashboard-header__eyebrow">vanpi controls</p>
          <h1>
            <button
              className="dashboard-header__projects"
              type="button"
              aria-haspopup="dialog"
              aria-expanded={projectsOpen}
              aria-label="Van Dashboard — open hosted projects"
              onClick={() => setProjectsOpen(true)}
            >
              Van Dashboard<span aria-hidden="true">▾</span>
            </button>
          </h1>
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
      <ProjectDirectory open={projectsOpen} onClose={() => setProjectsOpen(false)} />
    </>
  );
}
