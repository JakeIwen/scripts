// Where each prompt row of the transcript came from. Shared by the exchange
// view (exchanges.ts) and `/clean turns` (turn-endings.ts): the person's own
// prompts and slash commands open an exchange; worker messages, notices and
// wake-ups are injected into one; tool results, command output and skill
// bodies are neither. Pure: no engine access.
import type { SessionMessage } from 'claude-code'

import type { PromptRecord } from '../types'

/** Origins the person produced themselves (PromptOrigin kinds). */
export const TYPED_KINDS: ReadonlySet<string> = new Set(['composer', 'bridge', 'sdk'])
export const COMMAND_KIND = 'command'
/** A `!command` the person ran in Codex: their shell, not a prompt to the model. */
export const SHELL_KIND = 'shell'
/** Rows that neither open an exchange nor earn a divider inside one. */
export const SILENT_KINDS: ReadonlySet<string> = new Set(['tool-result', 'command-output', 'skill', SHELL_KIND])
/** An unrecorded, unmarked row once the mod has been recording origins. */
export const UNKNOWN_KIND = 'other'
/** The kind `classifyRows` gives an assistant row. */
export const ASSISTANT_KIND = ''

const KIND_LABELS: Record<string, string> = {
  composer: 'your prompt',
  bridge: 'your prompt',
  sdk: 'your prompt',
  'task-notification': 'a background task finishing',
  'scheduled-trigger': 'a scheduled wake-up',
  goal: 'a goal continuation',
  peer: 'a worker message',
  'peer-send-message': 'a worker message',
  command: 'a slash command',
  interrupt: 'an interruption',
  compaction: 'a context compaction',
  other: 'an injected message',
}

export const kindLabel = (kind: string): string => KIND_LABELS[kind] ?? kind

/** Text markers for rows the mod never saw submitted (before it loaded, or not a submission). */
const MARKERS: [RegExp, string][] = [
  [/^\s*<task-notification>/, 'task-notification'],
  [/^\s*Another Claude session sent a message|^\s*<agent-message/, 'peer'],
  [/^\s*<command-(name|message)>/, 'command'],
  [/^\s*<local-command-(stdout|stderr|caveat)>/, 'command-output'],
  [/^\s*Base directory for this skill:/, 'skill'],
  [/^\s*\[Request interrupted/, 'interrupt'],
  [/^\s*This session is being continued from a previous conversation/, 'compaction'],
]

const stripReminders = (text: string): string => text.replace(/<system-reminder>[\s\S]*?<\/system-reminder>/g, '')
const collapse = (text: string): string => text.replace(/\s+/g, ' ').trim()

/** Collapsed whitespace, cut to the length a record keeps. */
export const promptHead = (text: string): string => collapse(text).slice(0, 160)

function recordedKind(text: string, records: readonly PromptRecord[]): string | undefined {
  const flat = collapse(stripReminders(text))
  for (const { head, kind } of [...records].reverse()) {
    if (head !== '' && flat.startsWith(head)) return kind
  }
  return undefined
}

/**
 * The kind of every row: ASSISTANT_KIND for assistant rows. A recorded origin
 * wins, then a text marker. An unmatched row counts as typed only before the
 * first recorded one, since from then on every submission was recorded.
 */
export function classifyRows(messages: readonly SessionMessage[], records: readonly PromptRecord[]): string[] {
  const recorded = messages.map(m =>
    m.role === 'user' && !(m.toolResults && m.toolResults.length > 0) ? recordedKind(m.text, records) : undefined,
  )
  const firstRecorded = recorded.findIndex(kind => kind !== undefined)
  return messages.map((m, i) => {
    if (m.role === 'assistant') return ASSISTANT_KIND
    if (m.toolResults && m.toolResults.length > 0) return 'tool-result'
    const marker = MARKERS.find(([re]) => re.test(m.text))?.[1]
    const fallback = firstRecorded >= 0 && i > firstRecorded ? UNKNOWN_KIND : 'composer'
    return recorded[i] ?? marker ?? fallback
  })
}

/** Back-compat single-row form of `classifyRows`. */
export const promptKind = (text: string, records: readonly PromptRecord[]): string =>
  classifyRows([{ role: 'user', text, toolUses: [] }], records)[0] ?? 'composer'

const MAX_PROMPT_CHARS = 3000

/** The prompt as the pane shows it: a slash command as typed, else the text without reminders. */
export function promptDisplay(text: string, kind: string): string {
  if (kind === COMMAND_KIND) {
    const name = /<command-name>([\s\S]*?)<\/command-name>/.exec(text)?.[1]?.trim() ?? ''
    const args = /<command-args>([\s\S]*?)<\/command-args>/.exec(text)?.[1]?.trim() ?? ''
    if (name !== '') return `${name.startsWith('/') ? name : `/${name}`}${args ? ` ${args}` : ''}`
  }
  const shown = stripReminders(text)
    .replace(/<\/?pasted_content[^>]*>/g, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
  return shown.length > MAX_PROMPT_CHARS ? `${shown.slice(0, MAX_PROMPT_CHARS - 1)}…` : shown
}
