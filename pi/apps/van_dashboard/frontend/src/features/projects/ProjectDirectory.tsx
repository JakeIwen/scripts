import { useState } from 'react';

import { BottomSheet } from '../../components/BottomSheet';
import { HOSTED_PROJECTS } from './catalog';
import './projects.css';

export function ProjectDirectory({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [search, setSearch] = useState('');
  const query = search.trim().toLowerCase();
  const projects = HOSTED_PROJECTS.filter((project) =>
    `${project.name} ${project.description} ${project.host}`.toLowerCase().includes(query),
  );

  return (
    <BottomSheet
      open={open}
      title="Hosted projects"
      description="Your web apps in one place. Use LAN on the van network, or Tailscale when away."
      onClose={onClose}
    >
      <div className="project-directory__intro">
        <label htmlFor="project-search">Find a project</label>
        <input
          id="project-search"
          type="search"
          placeholder="Search projects…"
          value={search}
          onChange={(event) => setSearch(event.currentTarget.value)}
        />
        <p>“This Mac” links require the app running on your Mac and must be opened there.</p>
      </div>
      <div className="project-directory__list">
        {projects.map((project) => (
          <article className="project-directory__card" key={project.id}>
            <header>
              <h3>
                <span aria-hidden="true">{project.icon}</span> {project.name}
              </h3>
              <span className="project-directory__host">{project.host}</span>
            </header>
            <p>{project.description}</p>
            <div className="project-directory__links">
              {project.links.map((link) => (
                <a href={link.url} target="_blank" rel="noopener noreferrer" key={link.label}>
                  <strong>
                    {link.label} <span aria-hidden="true">↗</span>
                  </strong>
                  <span>{link.url}</span>
                </a>
              ))}
            </div>
            {project.note && <small>{project.note}</small>}
          </article>
        ))}
      </div>
      {projects.length === 0 && <p>No projects match your search.</p>}
    </BottomSheet>
  );
}
