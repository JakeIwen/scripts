import { BottomSheet } from '../../components/BottomSheet';
import { HOSTED_PROJECTS } from './catalog';
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
        {projects.map((project) => (
          <li className="project-directory__row" key={project.id}>
            <span title={[project.description, project.note].filter(Boolean).join(' ')}>
              {project.name}
            </span>
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
                </a>
              ))}
            </div>
          </li>
        ))}
      </ul>
      <p className="project-directory__legend">
        LAN: van network · TS: Tailscale · This Mac: local apps
      </p>
    </BottomSheet>
  );
}
