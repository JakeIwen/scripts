import { getJson, postForm, postJson } from '../../api/client';
import {
  decodeHostedProjectsResponse,
  PROJECT_LINK_KIND,
  PROJECT_STATUS,
  type HostedProject,
  type ProjectLink,
} from './schema';

export const PROJECT_URL_FIELDS = [
  {
    kind: PROJECT_LINK_KIND.LOCAL,
    label: 'This Mac',
    placeholder: 'http://127.0.0.1:4328/',
  },
  { kind: PROJECT_LINK_KIND.LAN, label: 'LAN', placeholder: 'http://vanpi.lan:8788/' },
  { kind: PROJECT_LINK_KIND.TAILSCALE, label: 'TS', placeholder: 'http://100.82.91.76:8788/' },
  { kind: PROJECT_LINK_KIND.WEB, label: 'Web', placeholder: 'https://example.com/' },
] as const;

export type ProjectDraft = Record<'name' | ProjectLink['kind'], string>;
export type ProjectPatch = {
  name?: string;
  description?: string;
  status?: typeof PROJECT_STATUS.LISTED | typeof PROJECT_STATUS.HIDDEN;
  links?: ProjectLink[];
};

export async function fetchHostedProjects(signal: AbortSignal): Promise<HostedProject[]> {
  return decodeHostedProjectsResponse(await getJson('/api/hosted-projects', signal));
}

export async function addHostedProject(draft: ProjectDraft): Promise<HostedProject[]> {
  return decodeHostedProjectsResponse(await postForm('/api/hosted-projects', draft));
}

export async function updateHostedProject(
  id: string,
  patch: ProjectPatch,
): Promise<HostedProject[]> {
  const payload = await postJson(`/api/hosted-projects/${encodeURIComponent(id)}`, patch);
  return decodeHostedProjectsResponse(payload);
}
