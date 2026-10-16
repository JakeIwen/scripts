import { atom, read, update } from 'claude-code'
import type { Register } from 'claude-code'

import type { CleanView } from '../types'
import { cleanTurn, parseArgs } from './clean'

const PANE = 'clean'
const EMPTY: CleanView = { text: '', label: '' }
const view = atom({ plugin: 'clean-output', key: 'view' } as const, EMPTY)

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'clean',
      description: 'Show the last reply without tool calls. Args: N (replies back), copy, tools',
    })
    return next(e)
  })

  on('command.run', { command: 'clean' }, async ($, e) => {
    const opts = parseArgs(e.args)
    const cleaned = cleanTurn(await $.session.messages(), opts)
    if (cleaned === undefined) return { text: 'clean: no reply found yet.' }
    if (opts.copy) {
      const copied = await $.ui.copy({ text: cleaned.text })
      return {
        text: copied.isCopied ? `clean: copied the ${cleaned.label}.` : `clean: copy failed (${copied.reason}).`,
      }
    }
    await update($, view, () => cleaned)
    const opened = await $.ui.open({ id: PANE, title: 'Clean', focus: true, closeOnEscape: true, rows: 40 })
    return { text: opened.isPlaced ? '' : `clean: pane not placed (${opened.reason}).` }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Markdown, Button } = $.ui.resolve(e)
    const current = await read($, view)
    return (
      <Box flexDirection="column">
        <Box>
          <Text dimColor>{current.label || 'run /clean'} · </Text>
          <Button hotkey="c" plain onPress={() => void $.ui.copy({ text: current.text })}>
            copy
          </Button>
          <Text dimColor> · esc closes · arrows scroll</Text>
        </Box>
        <Markdown text={current.text || '_Nothing to show yet._'} />
      </Box>
    )
  })
}
