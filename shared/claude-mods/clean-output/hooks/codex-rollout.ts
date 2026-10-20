// A Codex session file (`~/.codex/sessions/**/rollout-*.jsonl`) as the rows
// and prompt records the exchange and turn views read, so codexclean shares
// their grouping and filters with /clean. Codex marks each turn's trigger and
// each typed prompt itself, so every row's origin is recorded, never guessed.
// Pure: fed one line at a time, no file or engine access.
import type { SessionMessage, ToolUseSummary } from 'claude-code'

import type { PromptRecord } from '../types'
import { SHELL_KIND, UNKNOWN_KIND, promptHead } from './prompt-kinds'

export type RolloutMeta = {
  id: string
  cwd: string
  /** `user` for a session the person started, `subagent` for a worker's. */
  threadSource: string
}

export type Rollout = {
  meta: RolloutMeta | undefined
  messages: SessionMessage[]
  records: PromptRecord[]
}

/** A turn's `turn_trigger` as a prompt kind; a turn with none was the person's. */
const TRIGGER_KINDS: Record<string, string> = { user: 'composer', exec: 'sdk', goal: 'goal' }

/** The row text standing in for a turn or event that had no prompt of its own. */
const PLACEHOLDERS: Record<string, string> = {
  goal: '[Codex: goal continuation]',
  compaction: '[Codex: context compaction]',
  [UNKNOWN_KIND]: '[Codex: turn without a prompt]',
}

type Json = Record<string, unknown>
const obj = (value: unknown): Json =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? (value as Json) : {}
const str = (value: unknown): string => (typeof value === 'string' ? value : '')
const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : [])

/** A message's content blocks as text; an image or skill as a short marker. */
function contentText(content: unknown): string {
  if (typeof content === 'string') return content
  return list(content)
    .map(block => {
      const b = obj(block)
      const type = str(b.type)
      if (type === 'text' || type === 'Text' || type === 'input_text' || type === 'output_text') return str(b.text)
      if (type.includes('image')) return '[image]'
      if (type === 'skill') return `[skill ${str(b.name)}]`.replace(' ]', ']')
      return ''
    })
    .filter(text => text !== '')
    .join('\n')
}

/** A command as one line: the parsed form Codex shows, else the shell's last argument. */
function commandLine(item: Json): string {
  const parsed = list(item.parsed_cmd).map(p => str(obj(p).cmd)).filter(Boolean)
  if (parsed.length > 0) return parsed.join(' && ')
  const command = item.command
  return typeof command === 'string' ? command : str(list(command).at(-1))
}

const use = (id: unknown, tool: string, input: Json): ToolUseSummary => ({ tool_use_id: str(id), tool, input })

/** A tool item (current format) or tool event (older files) as the tool use the filters summarise. */
function toolUse(item: Json): ToolUseSummary | undefined {
  switch (str(item.type)) {
    case 'CommandExecution':
    case 'exec_command_end':
      return use(item.id ?? item.call_id, 'shell', { command: commandLine(item) })
    case 'McpToolCall':
      return use(item.id, `${str(item.server)}.${str(item.tool)}`, obj(item.arguments))
    case 'mcp_tool_call_end': {
      const call = obj(item.invocation)
      return use(item.call_id, `${str(call.server)}.${str(call.tool)}`, obj(call.arguments))
    }
    case 'FileChange':
    case 'patch_apply_end':
      return use(item.id ?? item.call_id, 'apply_patch', { file_path: Object.keys(obj(item.changes)).join(', ') })
    case 'Extension':
    case 'web_search_end':
      return use(item.id ?? item.call_id, 'web_search', { pattern: str(item.query) || str(item.kind) })
    case 'ImageView':
      return use(item.id, 'view_image', { file_path: str(item.path) })
    case 'SubAgentActivity':
      return use(item.id, 'agent', { description: `${str(item.kind)} ${str(item.agent_path)}`.trim() })
    case 'CollabAgentToolCall':
      return use(item.id, 'agent', { description: str(item.tool) })
    default:
      return undefined
  }
}

/** Rows built so far, and the turn being read. */
type State = Rollout & { turnKind: string; isPrompted: boolean; hasItems: boolean }

function addUser(state: State, text: string, kind: string): void {
  state.messages.push({ role: 'user', text, toolUses: [] })
  state.records.push({ head: promptHead(text), kind })
  state.isPrompted = true
}

/** Output in a turn nothing prompted (a goal continuation, a wake) gets a placeholder row first. */
function ensurePrompt(state: State): void {
  if (state.isPrompted) return
  const kind = state.turnKind === 'composer' ? UNKNOWN_KIND : state.turnKind
  addUser(state, PLACEHOLDERS[kind] ?? PLACEHOLDERS[UNKNOWN_KIND] ?? '', kind)
}

function addAssistant(state: State, text: string, tool?: ToolUseSummary): void {
  ensurePrompt(state)
  state.messages.push({ role: 'assistant', text, toolUses: tool ? [tool] : [] })
}

/** One completed item: a prompt, a reply, a tool call, a compaction. */
function addItem(state: State, item: Json): void {
  const type = str(item.type)
  if (type === 'UserMessage') {
    addUser(state, contentText(item.content), state.turnKind)
  } else if (type === 'AgentMessage') {
    addAssistant(state, contentText(item.content))
  } else if (type === 'CommandExecution' && str(item.source) === 'user_shell') {
    // The person's own `!command`: its output never reached the model as a reply.
    addUser(state, `!${commandLine(item)}`, SHELL_KIND)
  } else if (type === 'ContextCompaction') {
    addUser(state, PLACEHOLDERS.compaction ?? '', 'compaction')
  } else {
    const tool = toolUse(item)
    if (tool) addAssistant(state, '', tool)
  }
}

/** One event of a file written before Codex logged items (before about 2026-09). */
function addLegacyEvent(state: State, event: Json): void {
  const type = str(event.type)
  if (type === 'user_message') addUser(state, str(event.message), state.turnKind)
  else if (type === 'agent_message') addAssistant(state, str(event.message))
  else if (type === 'context_compacted') addUser(state, PLACEHOLDERS.compaction ?? '', 'compaction')
  else {
    const tool = toolUse(event)
    if (tool) addAssistant(state, '', tool)
  }
}

function addLine(state: State, line: string): void {
  let record: Json
  try {
    record = obj(JSON.parse(line))
  } catch {
    return // a line still being written, or not JSON
  }
  const payload = obj(record.payload)
  if (record.type === 'session_meta') {
    state.meta = { id: str(payload.id), cwd: str(payload.cwd), threadSource: str(payload.thread_source) }
  } else if (record.type === 'event_msg' && payload.type === 'task_started') {
    const trigger = str(obj(payload.turn_attribution).turn_trigger)
    state.turnKind = TRIGGER_KINDS[trigger] ?? 'composer'
    state.isPrompted = false
  } else if (record.type === 'event_msg' && payload.type === 'item_completed') {
    state.hasItems = true
    addItem(state, obj(payload.item))
  } else if (record.type === 'event_msg' && !state.hasItems) {
    addLegacyEvent(state, payload)
  }
}

/** A reader to feed the file's lines in order; `rollout()` is the result so far. */
export function createRolloutReader(): { add: (line: string) => void; rollout: () => Rollout } {
  const state: State = { meta: undefined, messages: [], records: [], turnKind: 'composer', isPrompted: false, hasItems: false }
  return {
    add: line => addLine(state, line),
    rollout: () => ({ meta: state.meta, messages: [...state.messages], records: [...state.records] }),
  }
}

/** A whole file's text as a rollout. */
export function readRollout(text: string): Rollout {
  const reader = createRolloutReader()
  text.split('\n').forEach(line => reader.add(line))
  return reader.rollout()
}
