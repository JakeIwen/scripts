import * as v from 'valibot';

import {
  booleanValue,
  decode,
  nullableString,
  object,
  oneOf,
  text,
  trueValue,
} from '../../api/schema';

export const PROJECT_HOST = {
  MAC: 'mac',
  PI: 'pi',
  WEB: 'web',
  CUSTOM: 'custom',
} as const;

export const PROJECT_LINK_KIND = {
  LOCAL: 'local',
  LAN: 'lan',
  TAILSCALE: 'ts',
  WEB: 'web',
} as const;

export const PROJECT_STATUS = {
  NEW: 'new',
  LISTED: 'listed',
  HIDDEN: 'hidden',
} as const;

export const PROJECT_SOURCE = {
  SCANNED: 'scanned',
  MANUAL: 'manual',
} as const;

const safeProjectUrl = v.pipe(
  text,
  v.check((value) => {
    try {
      const parsed = new URL(value);
      return ['http:', 'https:'].includes(parsed.protocol) && !parsed.username && !parsed.password;
    } catch {
      return false;
    }
  }, 'must be a safe http or https URL without credentials'),
);

export const projectLinkSchema = object({
  kind: oneOf([
    PROJECT_LINK_KIND.LOCAL,
    PROJECT_LINK_KIND.LAN,
    PROJECT_LINK_KIND.TAILSCALE,
    PROJECT_LINK_KIND.WEB,
  ]),
  label: text,
  url: safeProjectUrl,
});

export const hostedProjectSchema = object({
  id: text,
  key: nullableString,
  name: text,
  description: text,
  host: oneOf([PROJECT_HOST.MAC, PROJECT_HOST.PI, PROJECT_HOST.WEB, PROJECT_HOST.CUSTOM]),
  links: v.pipe(
    v.array(projectLinkSchema),
    v.minLength(1, 'must include at least one link'),
    v.maxLength(4, 'must include no more than four links'),
  ),
  status: oneOf([PROJECT_STATUS.NEW, PROJECT_STATUS.LISTED, PROJECT_STATUS.HIDDEN]),
  source: oneOf([PROJECT_SOURCE.SCANNED, PROJECT_SOURCE.MANUAL]),
  live: booleanValue,
  first_seen: nullableString,
  last_seen: nullableString,
  updated_at: text,
});

const hostedProjectsResponseSchema = v.pipe(
  object({ ok: trueValue, projects: v.array(hostedProjectSchema) }),
  v.transform((response) => response.projects),
);

export function decodeHostedProjectsResponse(value: unknown): HostedProject[] {
  return decode(hostedProjectsResponseSchema, value, 'project directory response');
}

export type ProjectLink = v.InferOutput<typeof projectLinkSchema>;
export type HostedProject = v.InferOutput<typeof hostedProjectSchema>;
export type ProjectStatus = HostedProject['status'];

const LINK_PRIORITY: Record<ProjectLink['kind'], number> = {
  [PROJECT_LINK_KIND.LOCAL]: 0,
  [PROJECT_LINK_KIND.LAN]: 1,
  [PROJECT_LINK_KIND.TAILSCALE]: 2,
  [PROJECT_LINK_KIND.WEB]: 3,
};

export function preferredProjectLink(links: ProjectLink[]): ProjectLink | undefined {
  return [...links].sort((left, right) => LINK_PRIORITY[left.kind] - LINK_PRIORITY[right.kind])[0];
}
