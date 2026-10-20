// codexclean: Claude's /clean for Codex sessions. Reads a Codex session file
// and shows its exchanges without tool calls, through the same grouping,
// filters and text rendering as the clean-output mod; build.mjs lays this file
// next to those modules (../hooks/*.mjs), so it runs only from a build.
import { spawnSync } from 'node:child_process'
import { closeSync, existsSync, fstatSync, mkdirSync, openSync, readdirSync, readFileSync, readSync, realpathSync, statSync, writeFileSync } from 'node:fs'
import { homedir, tmpdir } from 'node:os'
import { basename, join } from 'node:path'
import { StringDecoder } from 'node:string_decoder'
import { pathToFileURL } from 'node:url'

import { parseArgs } from '../hooks/clean.mjs'
import { viewText } from '../hooks/clean-view.mjs'
import { createRolloutReader } from '../hooks/codex-rollout.mjs'
import { cleanExchanges } from '../hooks/exchanges.mjs'
import { markdownToHtml, markdownToPlain } from '../hooks/markdown.mjs'
import { turnEndings } from '../hooks/turn-endings.mjs'

const USAGE = `usage: codexclean [N] [turns] [tools] [md] [copy | open | watch | print] [--session <id|file>]

Shows a Codex session's exchanges (each prompt, then the reply without tool
calls). The session is --session, else this Codex session ($CODEX_THREAD_ID),
else the newest one started in this folder.

  N        only the last N exchanges (watch: 1 unless given)
  turns    each turn's last message since your last prompt
  tools    keep one line per tool call
  md       markdown instead of plain text (print, copy)
  copy     copy the text to the clipboard
  open     save it as a page in ~/.agent/artifacts and open it
  watch    redraw the newest exchange as Codex writes it (its own pane)
  print    print even inside Codex, where output joins the model's context

Inside Codex, a !command's output joins the model's context, so a bare
!codexclean saves and opens the page instead of printing the text.`

const MODES = ['copy', 'open', 'watch', 'print']
const BAND = '\x1b[48;5;238m'
const DIM = '\x1b[2m'
const RESET = '\x1b[0m'
const codexHome = env => env.CODEX_HOME || join(homedir(), '.codex')
const ART_SCRIPT = join(homedir(), '.codex/skills/codex-artifact/scripts/agent_art.py')
const PAGES = join(homedir(), '.local/state/codexclean/pages.json')

export function parseCli(argv) {
  const words = []
  let session
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i]
    if (arg === '-h' || arg === '--help') return { help: true }
    if (arg === '--session' || arg === '-s') session = argv[(i += 1)]
    else words.push(arg)
  }
  return {
    opts: parseArgs(words.join(' ')),
    mode: MODES.find(mode => words.includes(mode)),
    hasCount: words.some(word => /^\d+$/.test(word)),
    session,
  }
}

/** A `!command` in Codex is captured rather than shown, and its output joins the model's context. */
export const chooseMode = (mode, { isTTY, inCodex }) => mode ?? (inCodex && !isTTY ? 'open' : 'print')

/** Every session file, newest first. */
function sessionFiles(env) {
  const root = join(codexHome(env), 'sessions')
  if (!existsSync(root)) return []
  return readdirSync(root, { recursive: true })
    .map(String)
    .filter(rel => /(^|\/)rollout-[^/]*\.jsonl$/.test(rel))
    .map(rel => ({ file: join(root, rel), mtime: statSync(join(root, rel)).mtimeMs }))
    .sort((a, b) => b.mtime - a.mtime)
    .map(entry => entry.file)
}

/** The session_meta payload on a file's first line (which can run to tens of KB). */
function metaOf(file) {
  const fd = openSync(file, 'r')
  try {
    const chunk = Buffer.alloc(1 << 16)
    const parts = []
    for (let total = 0; total < 1 << 22; ) {
      const n = readSync(fd, chunk, 0, chunk.length, total)
      if (n === 0) break
      const end = chunk.subarray(0, n).indexOf(10)
      parts.push(Buffer.from(chunk.subarray(0, end < 0 ? n : end)))
      if (end >= 0) break
      total += n
    }
    return JSON.parse(Buffer.concat(parts).toString('utf8')).payload ?? {}
  } catch {
    return {}
  } finally {
    closeSync(fd)
  }
}

/** A folder as the filesystem resolves it, so a symlinked path matches its target. */
function resolved(path) {
  try {
    return realpathSync(path)
  } catch {
    return path
  }
}

export function findSession({ session, env, cwd }) {
  if (session && existsSync(session)) return session
  const files = sessionFiles(env)
  const id = session || env.CODEX_THREAD_ID
  if (id) {
    const hit = files.find(file => basename(file).includes(id))
    if (hit) return hit
    throw new Error(`no Codex session file matches ${id}`)
  }
  const here = resolved(cwd)
  for (const file of files.slice(0, 500)) {
    const meta = metaOf(file)
    if (typeof meta.cwd === 'string' && resolved(meta.cwd) === here && meta.thread_source !== 'subagent') return file
  }
  throw new Error(`no Codex session started in ${cwd}; pass --session <id>`)
}

/** Reads a session file as it grows: each `read()` feeds only the lines appended since the last. */
export function sessionReader(file) {
  const reader = createRolloutReader()
  const decoder = new StringDecoder('utf8')
  let offset = 0
  let partial = ''
  return {
    read() {
      const fd = openSync(file, 'r')
      try {
        const size = fstatSync(fd).size
        const chunk = Buffer.alloc(1 << 20)
        while (offset < size) {
          const n = readSync(fd, chunk, 0, Math.min(chunk.length, size - offset), offset)
          if (n === 0) break
          offset += n
          const lines = (partial + decoder.write(chunk.subarray(0, n))).split('\n')
          partial = lines.pop() ?? ''
          lines.forEach(line => reader.add(line))
        }
      } finally {
        closeSync(fd)
      }
      return reader.rollout()
    },
  }
}

const buildView = (rollout, opts) =>
  opts.turns ? turnEndings(rollout.messages, rollout.records, opts) : cleanExchanges(rollout.messages, rollout.records, opts)

const sessionLabel = (view, meta) => `${view.label} · ${meta?.id?.slice(0, 8) ?? '?'} · ${meta?.cwd ?? ''}`

/** Breaks a prompt line into pieces that fit the band's width. */
const wrapLine = (line, width) => (line.length <= width ? [line] : [line.slice(0, width), ...wrapLine(line.slice(width), width)])

/** The view for a terminal: each prompt on a shaded band, the replies as plain text. */
export function terminalText(view, meta, columns) {
  const width = Math.max(20, columns - 2)
  const out = [`${DIM}${sessionLabel(view, meta)}${RESET}`]
  for (const section of view.sections) {
    out.push('')
    for (const line of section.prompt.split('\n').flatMap(l => wrapLine(l, width))) {
      out.push(`${BAND} ${line.padEnd(width)} ${RESET}`)
    }
    out.push(markdownToPlain(section.reply))
  }
  return out.join('\n')
}

const escapeHtml = text => text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

/**
 * Each prompt on its own band, as the terminal and the Claude pane shade the
 * person's messages. `--line` is the template's border tone, set for light
 * and dark; `--panel` would read as a code block.
 */
const PAGE_STYLE =
  '<style>.prompt { background: var(--line); padding: 8px 12px; border-radius: 6px; margin: 1.6rem 0 0.8rem; white-space: pre-wrap; overflow-wrap: anywhere; }</style>'

/** The view as a page body for agent_art.py: each prompt on a band above its reply. */
export function pageHtml(view, meta) {
  const head = `<p><code>${escapeHtml(meta?.cwd ?? '')}</code> · session <code>${escapeHtml(meta?.id ?? '')}</code> · ${escapeHtml(view.label)}</p>`
  const sections = view.sections.map(s => `<div class="prompt">${escapeHtml(s.prompt)}</div>\n${markdownToHtml(s.reply)}`)
  return [PAGE_STYLE, head, ...sections].join('\n')
}

function readPages() {
  try {
    return JSON.parse(readFileSync(PAGES, 'utf8'))
  } catch {
    return {}
  }
}

/** Saves the page (one per session, rewritten each time) and opens it on a Mac. */
function openPage(view, meta) {
  const pages = readPages()
  const input = join(tmpdir(), `codexclean-${process.pid}.html`)
  writeFileSync(input, pageHtml(view, meta))
  const args = [ART_SCRIPT, 'save', '--title', 'Codex exchanges', '--agent', 'codex', '--slug', `codexclean ${meta?.id?.slice(0, 8) ?? ''}`, '--input', input]
  const previous = meta?.id ? pages[meta.id] : undefined
  if (previous && existsSync(previous)) args.push('--out', previous)
  const cwd = meta?.cwd && existsSync(meta.cwd) ? meta.cwd : undefined
  const saved = spawnSync('python3', args, { cwd, encoding: 'utf8' })
  if (saved.status !== 0) return `codexclean: saving the page failed: ${(saved.stderr || saved.error?.message || '').trim()}`
  const field = name => new RegExp(`^${name}: (.*)$`, 'm').exec(saved.stdout)?.[1]
  const path = field('path')
  if (path && meta?.id) {
    mkdirSync(join(PAGES, '..'), { recursive: true })
    writeFileSync(PAGES, JSON.stringify({ ...pages, [meta.id]: path }, null, 1))
  }
  if (process.platform === 'darwin' && path) spawnSync('open', [path])
  const mac = field('mac')
  return `codexclean: ${view.label} → ${field('url') ?? path}${mac ? ` (on the Mac: ${mac})` : ''}`
}

/** The last lines of `text` that fill `rows` terminal rows `columns` wide. */
function fitTail(text, columns, rows) {
  const out = []
  let used = 0
  for (const line of text.split('\n').reverse()) {
    used += Math.max(1, Math.ceil(line.replace(/\x1b\[[0-9;]*m/g, '').length / columns))
    if (used > rows) break
    out.unshift(line)
  }
  return out
}

/** Redraws the newest exchange whenever the session file grows; ctrl+c stops. */
function watch(file, opts) {
  const reader = sessionReader(file)
  let last = ''
  const draw = () => {
    const rollout = reader.read()
    const columns = process.stdout.columns || 100
    const rows = process.stdout.rows || 30
    const view = buildView(rollout, opts)
    const text = view ? terminalText(view, rollout.meta, columns) : 'codexclean: no reply yet'
    const frame = `${fitTail(text, columns, rows - 1).join('\n')}\n${DIM}codexclean watch · ctrl+c stops${RESET}`
    if (frame === last) return
    last = frame
    process.stdout.write(`\x1b[H\x1b[2J${frame}`)
  }
  draw()
  setInterval(draw, 1000)
  process.on('SIGWINCH', () => ((last = ''), draw()))
  process.on('SIGINT', () => (process.stdout.write('\n'), process.exit(0)))
}

function show(text, isTTY) {
  if (!isTTY || text.split('\n').length < (process.stdout.rows || 24)) {
    process.stdout.write(`${text}\n`)
    return
  }
  // +G opens at the end: the newest exchange, as the Claude pane does.
  const paged = spawnSync('less', ['-R', '+G'], { input: text, stdio: ['pipe', 'inherit', 'inherit'] })
  if (paged.error) process.stdout.write(`${text}\n`)
}

export function main(argv, env = process.env) {
  const cli = parseCli(argv)
  if (cli.help) {
    console.log(USAGE)
    return 0
  }
  const isTTY = Boolean(process.stdout.isTTY)
  const mode = chooseMode(cli.mode, { isTTY, inCodex: Boolean(env.CODEX_THREAD_ID) })
  let file
  try {
    file = findSession({ session: cli.session, env, cwd: process.cwd() })
  } catch (error) {
    console.error(`codexclean: ${error.message}`)
    return 1
  }
  if (mode === 'watch') {
    if (isTTY) return watch(file, cli.hasCount ? cli.opts : { ...cli.opts, count: 1 })
    const id = basename(file).match(/([0-9a-f]{8}-[0-9a-f-]{27})\.jsonl$/)?.[1] ?? file
    console.log(`codexclean: watch needs its own terminal. In a split pane run: codexclean watch --session ${id}`)
    return 0
  }
  const rollout = sessionReader(file).read()
  const view = buildView(rollout, cli.opts)
  if (view === undefined) {
    console.log('codexclean: no reply found yet.')
    return 0
  }
  if (mode === 'open') {
    const note = cli.mode === undefined ? ' (inside Codex a bare !codexclean saves a page: printed text would join the context)' : ''
    console.log(openPage(view, rollout.meta) + note)
    return 0
  }
  const text = isTTY && mode === 'print' && !cli.opts.markdown ? terminalText(view, rollout.meta, process.stdout.columns || 100) : viewText(view, cli.opts)
  if (mode === 'copy') {
    const copied = spawnSync('pbcopy', { input: text })
    if (copied.status !== 0) {
      console.error(`codexclean: copy failed: ${(copied.stderr?.toString() || copied.error?.message || 'no pbcopy').trim()}`)
      return 1
    }
    console.log(`codexclean: copied the ${view.label} (${text.length} characters).`)
    return 0
  }
  show(text, isTTY)
  return 0
}

if (process.argv[1] && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href) {
  const code = main(process.argv.slice(2))
  if (typeof code === 'number') process.exitCode = code
}
