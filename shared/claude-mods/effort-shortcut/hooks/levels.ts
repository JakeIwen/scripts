import type { ModelEffort } from 'claude-code'

/** claudepm's effort letters (shared/model-selection in ~/config); Claude has no ultra. */
export const LEVELS: Record<string, ModelEffort> = { l: 'low', m: 'medium', h: 'high', x: 'xhigh', M: 'max' }

export const USAGE = 'usage: /e l|m|h|x|M (low, medium, high, xhigh, max)'

/** The /effort level a `/e` argument names, or undefined. */
export function levelFor(args: string): ModelEffort | undefined {
  return Object.hasOwn(LEVELS, args.trim()) ? LEVELS[args.trim()] : undefined
}
