import type { Register } from 'claude-code'

import { levelFor, USAGE } from './levels'

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'e', description: 'Set effort: l|m|h|x|M', argumentHint: 'l|m|h|x|M' })
    return next(e)
  })

  // Effort has no setter in the mod API, so forward to the built-in /effort.
  // $.command.run refuses calls from inside this hook, so it runs on the next
  // tick, once the hook has returned.
  on('command.run', { command: 'e' }, async ($, e) => {
    if (e.args.trim() === 'U') return { text: 'e: Claude has no ultra effort; M is max.' }
    const level = levelFor(e.args)
    if (level === undefined) return { text: USAGE }
    setTimeout(() => {
      $.command.run({ command: 'effort', args: level }).catch(err => $.ui.toast(`e: /effort ${level} failed: ${err}`))
    }, 0)
    return {}
  })
}
