// Builds codexclean: the mod's engine-free modules with their TypeScript
// types stripped (Node >= 22.13, `module.stripTypeScriptTypes`), plus the
// CLI, as plain ES modules. The output runs on any Node from 20, so the Pi
// needs no newer Node; only the machine that builds does.
//
// Usage: node build.mjs <out-dir>
import { cpSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { stripTypeScriptTypes } from 'node:module'
import { dirname, extname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const MOD = resolve(dirname(fileURLToPath(import.meta.url)), '..')

/** What the CLI imports, and what those import in turn. */
export const MODULES = [
  'hooks/clean.ts',
  'hooks/clean-view.ts',
  'hooks/codex-rollout.ts',
  'hooks/exchanges.ts',
  'hooks/markdown.ts',
  'hooks/prompt-kinds.ts',
  'hooks/turn-endings.ts',
]

/** Relative specifiers gain `.mjs`: Node's ES modules need the extension the sources leave out. */
const withExtensions = js =>
  js.replace(/(\bfrom\s+['"])(\.{1,2}\/[^'"]+)(['"])/g, (_, head, spec, tail) =>
    head + (extname(spec) ? spec : `${spec}.mjs`) + tail,
  )

export function build(outDir) {
  for (const rel of MODULES) {
    const js = stripTypeScriptTypes(readFileSync(join(MOD, rel), 'utf8'), { mode: 'strip' })
    const out = join(outDir, rel.replace(/\.ts$/, '.mjs'))
    mkdirSync(dirname(out), { recursive: true })
    writeFileSync(out, withExtensions(js))
  }
  mkdirSync(join(outDir, 'codex'), { recursive: true })
  cpSync(join(MOD, 'codex/codexclean.mjs'), join(outDir, 'codex/codexclean.mjs'))
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) {
  const outDir = process.argv[2]
  if (!outDir) {
    console.error('usage: node build.mjs <out-dir>')
    process.exit(2)
  }
  build(resolve(outDir))
}
