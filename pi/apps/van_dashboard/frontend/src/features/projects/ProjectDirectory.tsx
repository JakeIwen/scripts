import { BottomSheet } from '../../components/BottomSheet';
import { HOSTED_PROJECTS } from './catalog';
import './projects.css';

export function ProjectDirectory({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <BottomSheet
      open={open}
      title="Hosted projects"
      className="project-directory-sheet"
      onClose={onClose}
    >
      <div className="project-directory__list">
        {HOSTED_PROJECTS.map((project) => (
          <article className="project-directory__card" key={project.id}>
            <header title={[project.description, project.note].filter(Boolean).join(' ')}>
              <h3>
                <span aria-hidden="true">{project.icon}</span> {project.name}
              </h3>
            </header>
            <div className="project-directory__links">
              {project.links.map((link) => (
                <a
                  href={link.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  key={link.label}
                  title={link.url}
                  aria-label={`${project.name} · ${link.label} · ${link.url}`}
                >
                  {link.label === 'Tailscale' ? 'TS' : link.label}
                  <span aria-hidden="true">↗</span>
                </a>
              ))}
            </div>
          </article>
        ))}
      </div>
      <p className="project-directory__legend">
        LAN: van network · TS: Tailscale · This Mac: local apps
      </p>
    </BottomSheet>
  );
}
