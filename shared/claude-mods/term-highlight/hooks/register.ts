import type { Register } from 'claude-code'

import { highlightTerms } from './highlight'

export const register: Register = on => {
  // Changes only the drawing: the stored reply, which the glossary Stop hook
  // reads, keeps the text as written.
  on('ui.render', { component: 'AssistantMessage' }, ($, e, next) => {
    const text = highlightTerms(e.props.text)
    return next(text === e.props.text ? e : { ...e, props: { ...e.props, text } })
  })
}
