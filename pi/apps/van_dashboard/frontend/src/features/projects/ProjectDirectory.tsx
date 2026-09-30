import { BottomSheet } from '../../components/BottomSheet';
import { HOSTED_PROJECTS, preferredProjectLink } from './catalog';
import './projects.css';

const projects = [...HOSTED_PROJECTS].sort((a, b) => a.name.localeCompare(b.name));

export function ProjectDirectory({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <BottomSheet
      open={open}
      title="Hosted projects"
      className="project-directory-sheet"
      onClose={onClose}
    >
      <ul className="project-directory__list">
        {projects.map((project) => {
          const preferred = preferredProjectLink(project.links);
          return (
            <li className="project-directory__row" key={project.id}>
              <a
                className="project-directory__primary"
                href={preferred?.url}
                target="_blank"
                rel="noopener noreferrer"
                title={[project.description, project.note, preferred?.url]
                  .filter(Boolean)
                  .join(' ')}
              >
                {project.name}
              </a>
              <div className="project-directory__links">
                {project.links.map((link) => (
                  <a
                    href={link.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    key={link.label}
                    title={[link.url, link.note].filter(Boolean).join(' · ')}
                    aria-label={[project.name, link.label, link.url, link.note]
                      .filter(Boolean)
                      .join(' · ')}
                  >
                    {link.label === 'Tailscale' ? 'TS' : link.label}
                  </a>
                ))}
              </div>
            </li>
          );
        })}
      </ul>
      <p className="project-directory__legend">
        LAN: van network · TS: Tailscale · Local / This Mac: local apps · * Not live yet
      </p>
    </BottomSheet>
  );
}
