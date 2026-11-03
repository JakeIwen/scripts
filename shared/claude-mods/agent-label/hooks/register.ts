import type { EngineInterface, Register } from 'claude-code'

import { type Choice, frontmatter, isAlias, labelFor, mismatch, resolveChoice, sameModel, withLabel } from './label'

/** What a new agent was labelled: `asked` is the model the call or file named, `shown` what the label says. */
type Labelled = { description: string; asked: string; shown: Choice }

/** The main loop's key in `latest`; a subagent's is its agent id. */
const MAIN = ''
/** Each loop's latest request: a spawn it makes runs on this unless told otherwise. */
const latest = new Map<string, Choice>()
/** A labelled agent whose first request hasn't been seen yet. */
const labelled = new Map<string, Labelled>()
/** A new agent's first request, seen before its Agent call returned its id. */
const firstRequests = new Map<string, Choice>()
/** Bounds `firstRequests`: agents started with no Agent call (workflows) never claim theirs. */
const MAX_UNCLAIMED = 64

/** Alias → the model id it last resolved to (`sonnet` → `claude-sonnet-5-5`), kept across sessions. */
async function aliases($: EngineInterface): Promise<Record<string, string>> {
  const stored = await $.store.get('aliases')
  return typeof stored === 'object' && stored !== null ? (stored as Record<string, string>) : {}
}

/** The frontmatter of the agent file defining `type`, the project's before the user's; empty for built-ins. */
async function agentFile($: EngineInterface, type: string | undefined): Promise<Record<string, string>> {
  const home = await $.env.get('HOME')
  if (type === undefined || !/^[\w-]+$/.test(type) || home === undefined) return {}
  for (const dir of [`${await $.session.root()}/.claude/agents`, `${home}/.claude/agents`]) {
    const path = `${dir}/${type}.md`
    if (await $.fs.exists(path)) return frontmatter(await $.fs.read(path))
  }
  return {}
}

/** Checks an agent's first request against its label: learns what an alias resolved to, and says when the label was wrong. */
async function check($: EngineInterface, label: Labelled, observed: Choice) {
  if (isAlias(label.asked) && sameModel(label.asked, observed.model)) {
    const known = await aliases($)
    if (known[label.asked] !== observed.model) await $.store.set('aliases', { ...known, [label.asked]: observed.model })
  }
  const why = mismatch(label.shown, observed)
  if (why !== undefined) $.ui.toast(`agent-label: "${label.description}" ${why}`)
}

export const register: Register = on => {
  on('turn.step', async function* ($, e, next) {
    const key = e.agentId ?? MAIN
    const isFirst = e.agentId !== undefined && !latest.has(key)
    const seen: Choice = { model: e.model, effort: e.effort }
    latest.set(key, seen)
    if (isFirst) {
      const label = labelled.get(key)
      if (label !== undefined) {
        labelled.delete(key)
        await check($, label, seen)
      } else {
        if (firstRequests.size >= MAX_UNCLAIMED) firstRequests.clear()
        firstRequests.set(key, seen)
      }
    }
    return yield* next(e)
  })

  // The label goes in at the call, not at agent.spawn: only the call carries
  // its `effort` argument. The agent list shows the description it leaves.
  on('tool.call', { tool: 'Agent' }, async ($, e, next) => {
    const parent = latest.get(e.agentId ?? MAIN)
    if (parent === undefined) return next(e)
    const choice = resolveChoice(e, await agentFile($, e.subagent_type), parent)
    const shown = { ...choice, model: (await aliases($))[choice.model] ?? choice.model }
    const ran = await next({ ...e, description: withLabel(e.description, labelFor(shown, {})) })

    const result = ran.result as { agentId?: unknown } | undefined
    if (typeof result?.agentId !== 'string') return ran
    const label = { description: e.description, asked: choice.model, shown }
    const seen = firstRequests.get(result.agentId)
    if (seen === undefined) labelled.set(result.agentId, label)
    else {
      firstRequests.delete(result.agentId)
      await check($, label, seen)
    }
    return ran
  }).catch(($, e, next) => next(e)) // a failed label leaves the call as written
}
