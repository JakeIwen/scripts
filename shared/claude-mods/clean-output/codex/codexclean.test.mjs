// Builds codexclean into a temp folder and runs it against a synthetic Codex
// home: the CLI's own behaviour (session lookup, modes, page and clipboard),
// not the grouping, which hooks/codex-rollout.test.ts covers.
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { chmodSync, cpSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, writeFileSync } from 'node:fs'
import * as nodeModule from 'node:module'
import { homedir, tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const SKIP_BUILD =
  typeof nodeModule.stripTypeScriptTypes === 'function' ? false : 'building codexclean needs Node >= 22.13 (module.stripTypeScriptTypes)'
const ART_SCRIPT = join(homedir(), '.codex/skills/codex-artifact/scripts/agent_art.py')
const SKIP_ART = existsSync(ART_SCRIPT) ? false : `open needs the codex-artifact skill's ${ART_SCRIPT}`

const ID = '01a15000-1111-7222-8333-444455556666'
const line = (type, payload) => JSON.stringify({ type, payload })
const item = value => line('event_msg', { type: 'item_completed', item: value })

async function setup() {
  const root = mkdtempSync(join(tmpdir(), 'codexclean-test-'))
  const { build } = await import('./build.mjs')
  build(join(root, 'build'))
  const home = join(root, 'home')
  const work = join(root, 'work')
  const bin = join(root, 'bin')
  const sessions = join(home, '.codex/sessions/2026/10/19')
  for (const dir of [work, bin, sessions]) mkdirSync(dir, { recursive: true })
  const rollout = [
    line('session_meta', { id: ID, cwd: work, thread_source: 'user' }),
    line('event_msg', { type: 'task_started', turn_attribution: { turn_trigger: 'user' } }),
    item({ type: 'UserMessage', content: [{ type: 'text', text: 'fix the build' }] }),
    item({ type: 'AgentMessage', content: [{ type: 'Text', text: 'Checking the logs.' }] }),
    item({ type: 'CommandExecution', id: 'c', command: ['/bin/zsh', '-lc', 'npm test'], parsed_cmd: [{ cmd: 'npm test' }] }),
    item({ type: 'AgentMessage', content: [{ type: 'Text', text: 'Fixed: the **lockfile** was stale.' }] }),
  ]
  writeFileSync(join(sessions, `rollout-2026-10-19T10-00-00-${ID}.jsonl`), `${rollout.join('\n')}\n`)
  // Stand-ins that record what the CLI handed them.
  writeFileSync(join(bin, 'pbcopy'), `#!/bin/sh\ncat > "${root}/clipboard.txt"\n`)
  writeFileSync(join(bin, 'open'), `#!/bin/sh\necho "$@" > "${root}/opened.txt"\n`)
  chmodSync(join(bin, 'pbcopy'), 0o755)
  chmodSync(join(bin, 'open'), 0o755)
  const run = (args, extraEnv = {}) =>
    spawnSync(process.execPath, [join(root, 'build/codex/codexclean.mjs'), ...args], {
      cwd: work,
      encoding: 'utf8',
      env: { PATH: `${bin}:${process.env.PATH}`, HOME: home, ...extraEnv },
    })
  return { root, home, run }
}

test('prints the session started in this folder as plain text, tool calls dropped', { skip: SKIP_BUILD }, async () => {
  const { run } = await setup()
  const shown = run([])
  assert.equal(shown.status, 0, shown.stderr)
  assert.equal(shown.stdout, '❯ fix the build\nChecking the logs.\n\nFixed: the lockfile was stale.\n')
  assert.match(run(['md', '--session', ID.slice(0, 8)]).stdout, /^> fix the build\n\nChecking the logs\.\n\nFixed: the \*\*lockfile\*\*/)
  assert.match(run(['turns']).stdout, /^❯ Turn 1 of 1 · after your prompt\n {2}fix the build\nFixed: the lockfile was stale\.\n$/)
})

test('copy hands the plain text to pbcopy and prints one line', { skip: SKIP_BUILD }, async () => {
  const { root, run } = await setup()
  const copied = run(['copy'])
  assert.equal(copied.status, 0, copied.stderr)
  assert.match(copied.stdout, /^codexclean: copied the 1 exchange \(\d+ characters\)\.\n$/)
  assert.equal(readFileSync(join(root, 'clipboard.txt'), 'utf8'), '❯ fix the build\nChecking the logs.\n\nFixed: the lockfile was stale.')
})

test('inside Codex a bare call saves one page per session instead of printing', { skip: SKIP_BUILD || SKIP_ART }, async () => {
  const { root, home, run } = await setup()
  mkdirSync(dirname(join(home, '.codex/skills/codex-artifact/scripts/agent_art.py')), { recursive: true })
  cpSync(ART_SCRIPT, join(home, '.codex/skills/codex-artifact/scripts/agent_art.py'))
  for (let i = 0; i < 2; i += 1) {
    const saved = run([], { CODEX_THREAD_ID: ID })
    assert.equal(saved.status, 0, saved.stderr)
    assert.match(saved.stdout, /^codexclean: 1 exchange → file:\/\/.*\.html \(inside Codex a bare !codexclean saves a page/)
  }
  const pages = readdirSync(join(home, '.agent/artifacts')).filter(name => name.endsWith('.html') && name !== 'index.html')
  assert.equal(pages.length, 1)
  const page = readFileSync(join(home, '.agent/artifacts', pages[0]), 'utf8')
  assert.match(page, /<style>\.prompt \{ background: var\(--line\);/)
  assert.match(page, /<div class="prompt">fix the build<\/div>/)
  assert.match(page, /Fixed: the <strong>lockfile<\/strong> was stale\./)
  if (process.platform === 'darwin') assert.match(readFileSync(join(root, 'opened.txt'), 'utf8'), /\.html$/m)
})

test('watch outside a terminal names the command for a split pane; an unknown session fails', { skip: SKIP_BUILD }, async () => {
  const { run } = await setup()
  assert.equal(run(['watch'], { CODEX_THREAD_ID: ID }).stdout, `codexclean: watch needs its own terminal. In a split pane run: codexclean watch --session ${ID}\n`)
  const missing = run(['--session', 'deadbeef'])
  assert.equal(missing.status, 1)
  assert.equal(missing.stderr, 'codexclean: no Codex session file matches deadbeef\n')
})
