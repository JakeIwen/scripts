import { getJson, postForm } from '../../api/client';
import { arrayValue, objectValue, stringValue } from '../../api/validation';
import type { HostedProject, ProjectLink } from './catalog';

export const PROJECT_URL_FIELDS = [
  { kind: 'local', label: 'Local', placeholder: 'http://127.0.0.1:4328/' },
  { kind: 'lan', label: 'LAN', placeholder: 'http://vanpi.lan:8788/' },
  { kind: 'ts', label: 'TS', placeholder: 'http://100.82.91.76:8788/' },
  { kind: 'web', label: 'Web', placeholder: 'https://example.com/' },
] as const;

export type ProjectDraft = Record<'name' | ProjectLink['kind'], string>;

function decodeProjects(payload: unknown): HostedProject[] {
  const response = objectValue(payload, 'Project directory');
  if (response.ok !== true) throw new Error('Could not load project directory');
  return arrayValue(response.projects, 'projects').map((value) => {
    const project = objectValue(value, 'project');
    const links = arrayValue(project.links, 'project links').map((value): ProjectLink => {
      const link = objectValue(value, 'link');
      const field = PROJECT_URL_FIELDS.find((field) => field.kind === link.kind);
      if (!field) throw new Error('Unrecognized project URL type');
      const url = stringValue(link.url, 'project URL');
      const parsed = new URL(url);
      if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password) {
        throw new Error('Invalid saved project URL');
      }
      return { kind: field.kind, label: field.label, url };
    });
    if (!links.length) throw new Error('Saved project has no links');
    return {
      id: stringValue(project.id, 'project ID'),
      name: stringValue(project.name, 'project name'),
      host: 'Custom',
      description: '',
      links,
    };
  });
}

export async function fetchHostedProjects(signal: AbortSignal): Promise<HostedProject[]> {
  return decodeProjects(await getJson('/api/hosted-projects', signal));
}

export async function addHostedProject(draft: ProjectDraft): Promise<HostedProject[]> {
  return decodeProjects(await postForm('/api/hosted-projects', draft));
}
