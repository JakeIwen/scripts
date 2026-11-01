// Bolds the first mention of each term a reply's **Terms** block defines, so
// the word stands out where the prose uses it. Markdown in, markdown out: the
// engine still draws the reply, and markdown's only emphasis that reads as a
// highlight in the terminal is bold. Pure: no engine access.

// The block's shape is fixed by ~/.claude/TEACHING.md (the glossary hook
// parses the same lines): a `**Terms**` line, then `- **<term>**: <definition>`.
const TERMS_HEADING = /^\*\*Terms\*\*[ \t]*$/gm
const TERM_ENTRY = /^- \*\*(.+?)\*\*:/gm

// Spans a term inside them must not be bolded in: code, links, URLs and bold
// (nested bold markers break). A term the prose already bolds is left alone.
const FENCED = /^[ \t]*(`{3,}|~{3,})[^\n]*\n[\s\S]*?(?:^[ \t]*\1[ \t]*$|(?![\s\S]))/gm
const SHIELDS = [/`[^`\n]+`/g, /\[[^\]\n]*\]\([^)\n]*\)/g, /\b(?:https?|file):\/\/\S+/g]
const BOLD = /\*\*[^*\n]+\*\*/g

// Letters and digits, so "heap" doesn't match in "cheap" and a term that ends
// in punctuation ("O(n)") still has edges; `\b` gets both wrong.
const EDGE_BEFORE = '(?<![\\p{L}\\p{N}_])'
const EDGE_AFTER = '(?![\\p{L}\\p{N}_])'

type Span = { start: number; end: number; isBold: boolean }

const spansOf = (pattern: RegExp, text: string, isBold: boolean): Span[] =>
  [...text.matchAll(pattern)].map(match => ({ start: match.index, end: match.index + match[0].length, isBold }))

function shieldedSpans(text: string): Span[] {
  const fenced = spansOf(FENCED, text, false)
  return [
    ...fenced,
    ...SHIELDS.flatMap(pattern => spansOf(pattern, text, false)),
    ...spansOf(BOLD, text, true),
  ]
}

/** The terms the text's last **Terms** block defines, longest first. */
export function definedTerms(block: string): string[] {
  const terms = [...block.matchAll(TERM_ENTRY)].map(match => (match[1] ?? '').trim()).filter(Boolean)
  return [...new Set(terms)].sort((a, b) => b.length - a.length)
}

function termPattern(term: string): RegExp {
  const escaped = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '\\s+')
  // An optional plural, so "race conditions" counts as a mention of "race condition".
  return new RegExp(`${EDGE_BEFORE}${escaped}(?:e?s)?${EDGE_AFTER}`, 'giu')
}

function boldFirstMention(prose: string, term: string): string {
  const shields = shieldedSpans(prose)
  for (const match of prose.matchAll(termPattern(term))) {
    const start = match.index
    const end = start + match[0].length
    const shield = shields.find(span => start < span.end && end > span.start)
    // Bold around exactly this mention: the reply highlighted it itself.
    if (shield?.isBold && shield.start === start - 2 && shield.end === end + 2) return prose
    if (!shield) return `${prose.slice(0, start)}**${match[0]}**${prose.slice(end)}`
  }
  return prose
}

/** The reply's markdown with each defined term bolded at its first mention. */
export function highlightTerms(markdown: string): string {
  const heading = [...markdown.matchAll(TERMS_HEADING)].pop()
  if (heading === undefined) return markdown
  const prose = markdown.slice(0, heading.index)
  const block = markdown.slice(heading.index)
  // Longest first, so "hash map" is bolded before "hash" and shields it.
  const highlighted = definedTerms(block).reduce(boldFirstMention, prose)
  return highlighted + block
}
