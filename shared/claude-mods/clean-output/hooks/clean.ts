// Pure reply cleaning: no engine access, so it ports to a file-based reader
// (Codex rollouts) unchanged. A reply becomes a list of parts, and FILTERS
// turn that list into the text the pane shows. exchanges.ts and
// turn-endings.ts choose which replies to clean.
import type { SessionMessage } from 'claude-code'

import type { CleanOptions } from '../types'

export type { CleanOptions } from '../types'

export type Part =
  | { kind: 'text'; text: string }
  | { kind: 'tool'; text: string; tool: string }
  /** One dim line marking a worker message, notice or wake-up inside an exchange. */
  | { kind: 'divider'; text: string }

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
  {
    // A run of dividers with nothing between them keeps only its last; a
    // divider with nothing after it is dropped.
    name: 'collapse-dividers',
    apply: parts =>
      parts.filter((p, i) => p.kind !== 'divider' || (i + 1 < parts.length && parts[i + 1]?.kind !== 'divider')),
  },
]

export function parseArgs(args: string): CleanOptions {
  const opts: CleanOptions = { count: 'all', copy: false, markdown: false, tools: false, turns: false }
  for (const word of args.trim().split(/\s+/).filter(Boolean)) {
    if (/^\d+$/.test(word)) opts.count = Math.max(1, Number(word))
    else if (word === 'copy') opts.copy = true
    else if (word === 'md') opts.markdown = true
    else if (word === 'tools') opts.tools = true
    else if (word === 'turns') opts.turns = true
  }
  return opts
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
    if (m.role !== 'assistant') continue
    if (m.text) parts.push({ kind: 'text', text: m.text })
    for (const use of m.toolUses) parts.push({ kind: 'tool', tool: use.tool, text: summarizeTool(use) })
  }
  return parts
}
