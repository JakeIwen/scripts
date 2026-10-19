// Pure transcript cleaning: no engine access, so it ports to a file-based
// reader (Codex rollouts) unchanged. `cleanTurn` takes the session's messages
// and returns the chosen reply as markdown with tool calls removed.
import type { SessionMessage } from 'claude-code'

export type CleanOptions = {
  /** Which reply, counted back from the newest: 1 = the last one. */
  back: number
  /** Copy to the clipboard instead of opening the pane. */
  copy: boolean
  /** Keep one-line tool summaries instead of dropping tool calls. */
  tools: boolean
  /** Show each turn's ending since the last typed prompt (turn-endings.ts). */
  turns: boolean
}

export type Part =
  | { kind: 'text'; text: string }
  | { kind: 'tool'; text: string; tool: string }

/**
 * One filter over the reply's parts. Add a new one by appending to FILTERS:
 * it sees the parts the previous filters left, in order.
 */
export type Filter = {
  name: string
  apply: (parts: Part[], opts: CleanOptions) => Part[]
}

export const FILTERS: Filter[] = [
  {
    name: 'drop-tools',
    apply: (parts, opts) => (opts.tools ? parts : parts.filter(p => p.kind !== 'tool')),
  },
  {
    name: 'strip-context-blocks',
    apply: parts =>
      parts.map(p =>
        p.kind === 'text' ? { ...p, text: p.text.replace(/<context>[\s\S]*?<\/context>/g, '') } : p,
      ),
  },
  {
    name: 'squeeze-blank-lines',
    apply: parts => parts.map(p => ({ ...p, text: p.text.replace(/\n{3,}/g, '\n\n').trim() })),
  },
  {
    name: 'drop-empty',
    apply: parts => parts.filter(p => p.text !== ''),
  },
]

export function parseArgs(args: string): CleanOptions {
  const opts: CleanOptions = { back: 1, copy: false, tools: false, turns: false }
  for (const word of args.trim().split(/\s+/).filter(Boolean)) {
    if (/^\d+$/.test(word)) opts.back = Math.max(1, Number(word))
    else if (word === 'copy') opts.copy = true
    else if (word === 'tools') opts.tools = true
    else if (word === 'turns') opts.turns = true
  }
  return opts
}

/** A user message the person wrote (or a command row), not a tool-result carrier. */
const isPrompt = (m: SessionMessage): boolean =>
  m.role === 'user' && !(m.toolResults && m.toolResults.length > 0)

/** Groups assistant messages into replies, one group per user prompt. */
export function replies(messages: readonly SessionMessage[]): SessionMessage[][] {
  const out: SessionMessage[][] = []
  let current: SessionMessage[] = []
  for (const m of messages) {
    if (m.role === 'assistant') current.push(m)
    else if (isPrompt(m)) {
      if (current.length > 0) out.push(current)
      current = []
    }
  }
  if (current.length > 0) out.push(current)
  return out
}

const oneLine = (value: unknown, max = 100): string => {
  const s = typeof value === 'string' ? value : JSON.stringify(value) ?? ''
  const flat = s.replace(/\s+/g, ' ').trim()
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat
}

const summarizeTool = (use: SessionMessage['toolUses'][number]): string => {
  const input = use.input ?? {}
  const gist =
    typeof input.command === 'string' ? input.command
    : typeof input.file_path === 'string' ? input.file_path
    : typeof input.pattern === 'string' ? input.pattern
    : typeof input.description === 'string' ? input.description
    : input
  return `> \`${use.tool}\` ${oneLine(gist)}`
}

export function partsOf(reply: readonly SessionMessage[]): Part[] {
  const parts: Part[] = []
  for (const m of reply) {
    if (m.text) parts.push({ kind: 'text', text: m.text })
    for (const use of m.toolUses) parts.push({ kind: 'tool', tool: use.tool, text: summarizeTool(use) })
  }
  return parts
}

export type Cleaned = { text: string; label: string }

export function cleanTurn(
  messages: readonly SessionMessage[],
  opts: CleanOptions,
  filters: readonly Filter[] = FILTERS,
): Cleaned | undefined {
  const all = replies(messages)
  const reply = all[all.length - opts.back]
  if (reply === undefined) return undefined
  const parts = filters.reduce((acc, f) => f.apply(acc, opts), partsOf(reply))
  const text = parts.map(p => p.text).join('\n\n')
  const label = opts.back === 1 ? `last reply` : `reply ${opts.back} back`
  return { text, label: `${label} (${all.length} in session)` }
}
