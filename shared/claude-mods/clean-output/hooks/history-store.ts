// Where the rows each compaction ran over are kept: one JSON file per
// compaction under the Claude config directory, by session id, so a resumed
// session finds them too. One file each, since `$.fs.read` stops at 4 MiB.
import type { EngineInterface, SessionMessage } from 'claude-code'

import type { PromptRecord } from '../types'
import { keptCount, slimRow, type HistoryChunk } from './compaction-history'

async function historyDir($: EngineInterface): Promise<string | undefined> {
  const home = await $.env.get('HOME')
  const configDir = (await $.env.get('CLAUDE_CONFIG_DIR')) ?? (home === undefined ? undefined : `${home}/.claude`)
  return configDir === undefined ? undefined : `${configDir}/clean-output/history/${await $.session.id()}`
}

/** Saves the rows the compaction from `before` to `after` ran over. */
export async function saveCompaction(
  $: EngineInterface,
  before: readonly SessionMessage[],
  after: readonly SessionMessage[],
  records: readonly PromptRecord[],
): Promise<void> {
  const dir = await historyDir($)
  const kept = keptCount(before, after)
  if (dir === undefined || kept === before.length) return
  const chunk: HistoryChunk = { rows: before.map(slimRow), kept, records: [...records] }
  // Zero-padded time, so the names sort in the order the compactions ran.
  const name = String(await $.clock.now()).padStart(15, '0')
  await $.fs.write(`${dir}/${name}.json`, JSON.stringify(chunk))
}

/** Every saved compaction of this session, oldest first; none when there are none. */
export async function loadHistory($: EngineInterface): Promise<HistoryChunk[]> {
  const dir = await historyDir($)
  if (dir === undefined) return []
  const entries = await $.fs.list(dir).catch(() => [])
  const names = entries.filter(e => e.kind === 'file' && e.name.endsWith('.json')).map(e => e.name).sort()
  const chunks: HistoryChunk[] = []
  for (const name of names) {
    // A chunk that won't read or parse only loses those exchanges.
    const chunk = await $.fs.read(`${dir}/${name}`).then(text => JSON.parse(text) as HistoryChunk).catch(() => undefined)
    if (chunk && Array.isArray(chunk.rows)) chunks.push({ rows: chunk.rows, kept: chunk.kept ?? 0, records: chunk.records ?? [] })
  }
  return chunks
}
