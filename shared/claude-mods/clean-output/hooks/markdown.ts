// The reply markdown as plain text (what the pane shows, for copying) and as
// an HTML fragment (for codexclean's page). Line-based and small on purpose:
// replies use fences, headings, lists, tables, quotes and inline marks, not
// the whole CommonMark grammar. Pure: no engine access.

type Line =
  | { kind: 'fence'; text: string }
  | { kind: 'code'; text: string }
  | { kind: 'heading'; level: number; text: string }
  | { kind: 'rule' }
  | { kind: 'table'; cells: string[]; isDivider: boolean }
  | { kind: 'quote'; text: string }
  | { kind: 'item'; indent: string; marker: string; text: string; isOrdered: boolean }
  | { kind: 'blank' }
  | { kind: 'text'; text: string }

const FENCE = /^\s*(```|~~~)/
const HEADING = /^(#{1,6})\s+(.*?)\s*#*\s*$/
const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/
const ITEM = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/

const tableCells = (line: string): string[] =>
  line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(cell => cell.trim())

/** Each line of the markdown, classified; code inside a fence stays verbatim. */
function classify(markdown: string): Line[] {
  let isFenced = false
  return markdown.split('\n').map((text): Line => {
    if (FENCE.test(text)) {
      isFenced = !isFenced
      return { kind: 'fence', text }
    }
    if (isFenced) return { kind: 'code', text }
    if (text.trim() === '') return { kind: 'blank' }
    const heading = HEADING.exec(text)
    if (heading) return { kind: 'heading', level: heading[1]?.length ?? 1, text: heading[2] ?? '' }
    if (RULE.test(text)) return { kind: 'rule' }
    if (text.trim().startsWith('|')) {
      const cells = tableCells(text)
      return { kind: 'table', cells, isDivider: cells.every(cell => /^:?-+:?$/.test(cell)) }
    }
    if (/^\s*>/.test(text)) return { kind: 'quote', text: text.replace(/^\s*>\s?/, '') }
    const item = ITEM.exec(text)
    if (item) {
      const marker = item[2] ?? '-'
      return { kind: 'item', indent: item[1] ?? '', marker, text: item[3] ?? '', isOrdered: /\d/.test(marker) }
    }
    return { kind: 'text', text }
  })
}

type Span = { isCode: boolean; text: string }

/** Splits a line at code spans, so marks inside code are left alone. */
function spans(line: string): Span[] {
  const out: Span[] = []
  const re = /(`+)([\s\S]*?[^`])\1(?!`)/g
  let at = 0
  for (const match of line.matchAll(re)) {
    const index = match.index ?? 0
    if (index > at) out.push({ isCode: false, text: line.slice(at, index) })
    out.push({ isCode: true, text: (match[2] ?? '').trim() })
    at = index + match[0].length
  }
  if (at < line.length) out.push({ isCode: false, text: line.slice(at) })
  return out
}

/** Inline marks in prose, mapped by the renderer's functions. */
function marks(text: string, link: (label: string, url: string) => string, strong: (s: string) => string, em: (s: string) => string): string {
  return text
    .replace(/!\[([^\]]*)\]\(([^)\s]+)[^)]*\)/g, (_, alt: string) => alt)
    .replace(/\[([^\]]+)\]\(([^)\s]+)[^)]*\)/g, (_, label: string, url: string) => link(label, url))
    .replace(/<(https?:\/\/[^>\s]+)>/g, (_, url: string) => link(url, url))
    .replace(/(\*\*|__)(?=\S)([\s\S]*?\S)\1/g, (_, __, inner: string) => strong(inner))
    .replace(/~~(?=\S)([\s\S]*?\S)~~/g, (_, inner: string) => inner)
    .replace(/(^|[^\w*])\*(?=\S)([^*]*?\S)\*(?!\w)/g, (_, pre: string, inner: string) => pre + em(inner))
    .replace(/(^|[^\w])_(?=\S)([^_]*?\S)_(?!\w)/g, (_, pre: string, inner: string) => pre + em(inner))
    .replace(/\\([\\`*_{}[\]()#+\-.!|>~])/g, '$1')
}

const plainInline = (line: string): string =>
  spans(line)
    .map(s =>
      s.isCode
        ? s.text
        : marks(s.text, (label, url) => (label === url || url.startsWith('#') ? label : `${label} (${url})`), x => x, x => x),
    )
    .join('')

/** Pads each column of a run of table rows to its widest cell. */
function alignTable(rows: string[][]): string[] {
  const widths: number[] = []
  rows.forEach(row => row.forEach((cell, i) => (widths[i] = Math.max(widths[i] ?? 0, cell.length))))
  return rows.map(row => row.map((cell, i) => cell.padEnd(widths[i] ?? 0)).join('  ').trimEnd())
}

/** The markdown as the pane shows it, with the marks gone: what a copy should hold. */
export function markdownToPlain(markdown: string): string {
  const out: string[] = []
  let table: string[][] = []
  const flush = () => {
    if (table.length > 0) out.push(...alignTable(table))
    table = []
  }
  for (const line of classify(markdown)) {
    if (line.kind !== 'table') flush()
    switch (line.kind) {
      case 'fence':
        break
      case 'code':
        out.push(line.text)
        break
      case 'heading':
        out.push(plainInline(line.text))
        break
      case 'rule':
        out.push('─'.repeat(20))
        break
      case 'table':
        if (!line.isDivider) table.push(line.cells.map(plainInline))
        break
      case 'quote':
        out.push(`> ${plainInline(line.text)}`)
        break
      case 'item':
        out.push(`${line.indent}${line.marker} ${plainInline(line.text)}`)
        break
      case 'blank':
        out.push('')
        break
      case 'text':
        out.push(plainInline(line.text))
    }
  }
  flush()
  return out.join('\n')
}

const escapeHtml = (text: string): string =>
  text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

const htmlInline = (line: string): string =>
  spans(line)
    .map(s =>
      s.isCode
        ? `<code>${escapeHtml(s.text)}</code>`
        : marks(
            escapeHtml(s.text),
            (label, url) => `<a href="${url}">${label}</a>`,
            x => `<strong>${x}</strong>`,
            x => `<em>${x}</em>`,
          ),
    )
    .join('')

/** Closes whatever block is open when a line of another kind arrives. */
type Open = 'p' | 'ul' | 'ol' | 'pre' | 'table' | 'blockquote' | undefined

/** The markdown as an HTML fragment: paragraphs, headings, lists, tables, quotes, code. */
export function markdownToHtml(markdown: string): string {
  const out: string[] = []
  let open: Open
  let tableRows = 0
  let code: string[] = []
  const close = () => {
    if (open === 'pre') out.push(`<pre><code>${code.map(escapeHtml).join('\n')}</code></pre>`)
    else if (open === 'table') out.push('</tbody></table></div>')
    else if (open !== undefined) out.push(`</${open}>`)
    open = undefined
  }
  const begin = (block: Exclude<Open, undefined>, html: string) => {
    if (open !== block) {
      close()
      out.push(html)
      open = block
    }
  }
  for (const line of classify(markdown)) {
    if (line.kind === 'fence') {
      if (open === 'pre') close()
      else {
        close()
        open = 'pre'
        code = []
      }
    } else if (line.kind === 'code') {
      code.push(line.text)
    } else if (line.kind === 'table') {
      if (open !== 'table') tableRows = 0
      begin('table', '<div class="table-wrap"><table><tbody>')
      if (!line.isDivider) {
        const tag = tableRows === 0 ? 'th' : 'td'
        out.push(`<tr>${line.cells.map(cell => `<${tag}>${htmlInline(cell)}</${tag}>`).join('')}</tr>`)
        tableRows += 1
      }
    } else if (line.kind === 'item') {
      begin(line.isOrdered ? 'ol' : 'ul', line.isOrdered ? '<ol>' : '<ul>')
      out.push(`<li>${htmlInline(line.text)}</li>`)
    } else if (line.kind === 'quote') {
      begin('blockquote', '<blockquote>')
      out.push(`${htmlInline(line.text)}<br>`)
    } else if (line.kind === 'text') {
      begin('p', '<p>')
      out.push(htmlInline(line.text))
    } else {
      close()
      if (line.kind === 'heading') out.push(`<h4>${htmlInline(line.text)}</h4>`)
      if (line.kind === 'rule') out.push('<hr>')
    }
  }
  close()
  return out.join('\n')
}
