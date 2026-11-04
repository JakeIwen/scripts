/** A model and the effort it is asked to think at; effort is absent for a model that takes none. */
export type Choice = { model: string; effort?: string | number }

/** The arguments of an Agent call that decide what it runs on. */
export type AgentCall = { subagent_type?: string; model?: string; effort?: string }

/** A leading `[...] ` tag, ours or one a parent copied from an earlier label. */
const TAG = /^\[[^\]]*\]\s*/

/** Strips a context-size suffix such as `[1m]`. */
function bare(model: string): string {
  return model.replace(/\[[^\]]*\]$/, '')
}

/** `claude-sonnet-5-5` → `sonnet 5.5`; `claude-gpt-6.1-sol[1m]` → `gpt-6.1-sol`; an alias stays as is. */
export function shortModel(model: string): string {
  const id = bare(model).replace(/^claude-/, '').replace(/-\d{8}$/, '')
  return id.replace(/^(opus|sonnet|haiku|fable)-(\d+)-(\d+)$/, '$1 $2.$3')
}

/** True for a family alias (`sonnet`, `haiku`) rather than a model id. */
export function isAlias(model: string): boolean {
  return /^[a-z]+$/.test(model)
}

/** Whether a request's model is the one expected, an alias matching any id of its family. */
export function sameModel(expected: string, observed: string): boolean {
  return bare(expected) === bare(observed) || (isAlias(expected) && bare(observed).startsWith(`claude-${expected}-`))
}

/** A YAML scalar: a quoted one loses its quotes, a plain one its trailing ` # comment`. */
function scalar(raw: string): string {
  const quoted = /^(['"])(.*?)\1(?:\s+#.*)?$/.exec(raw)
  return quoted?.[2] ?? raw.replace(/\s+#.*$/, '')
}

/** The `key: value` lines of an agent file's YAML frontmatter, as plain strings. */
export function frontmatter(text: string): Record<string, string> {
  const block = /^---\r?\n([\s\S]*?)\r?\n---/.exec(text)?.[1] ?? ''
  const fields: Record<string, string> = {}
  for (const line of block.split(/\r?\n/)) {
    const m = /^([A-Za-z][\w-]*):\s*(.*?)\s*$/.exec(line)
    if (m?.[1] !== undefined && m[2] !== undefined) fields[m[1]] = scalar(m[2])
  }
  return fields
}

/**
 * What an Agent call runs on: the call's own `model` and `effort`, then its
 * agent file's frontmatter, then the parent's. A probe on 2.1.314 showed
 * built-in types with no model of their own (Explore included) take the
 * parent's model, and every agent takes the parent's effort unless the call
 * or its file sets one. A fork always runs on the parent's.
 */
export function resolveChoice(call: AgentCall, file: Record<string, string>, parent: Choice): Choice {
  if (call.subagent_type === 'fork') return parent
  const fileModel = file.model === 'inherit' ? undefined : file.model
  return { model: call.model ?? fileModel ?? parent.model, effort: call.effort ?? file.effort ?? parent.effort }
}

/** `[sonnet 5.5 · high]`. */
export function labelFor(choice: Choice): string {
  const model = shortModel(choice.model)
  return choice.effort === undefined ? `[${model}]` : `[${model} · ${choice.effort}]`
}

/** The description with `label` in front, replacing any tag already there. */
export function withLabel(description: string, label: string): string {
  return `${label} ${description.replace(TAG, '')}`
}

/** Why an agent's first request differs from its label, or undefined when it matches. */
export function mismatch(expected: Choice, observed: Choice): string | undefined {
  const sameEffort = String(expected.effort) === String(observed.effort)
  if (sameModel(expected.model, observed.model) && sameEffort) return undefined
  return `runs on ${labelFor(observed)}, not ${labelFor(expected)}`
}
