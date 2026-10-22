/**
 * The one-click fixes a check on the rail may carry (P19h): a house-style word replaced as a
 * whole word, and straight quotes turned typographic. The string work is here on its own so it
 * can be tested; `applyFix` runs it over a piece's editor text node by text node in one
 * transaction, so the marks stay and the editor announces the change like typing.
 */
import type { Editor } from '@tiptap/react'

export interface Fix {
  mode: string
  find: string
  replace: string
}

/** One replacement inside a string: `text` goes in for the characters `from` up to `to`. */
export interface Replacement {
  from: number
  to: number
  text: string
}

const WORD = /[\p{L}\p{N}_]/u

function isWordChar(ch: string): boolean {
  return ch !== '' && WORD.test(ch)
}

function isLetterOrDigit(ch: string): boolean {
  return ch !== '' && /[\p{L}\p{N}]/u.test(ch)
}

/** A quote here opens: at the start, or after a space, a bracket or a parenthesis. */
function opensAfter(prev: string): boolean {
  return prev === '' || /[\s([]/.test(prev)
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/** `replace` with its first letter in the case of `matched`'s first letter. */
export function withCapitalisationOf(matched: string, replace: string): string {
  const first = matched.charAt(0)
  if (!replace || !first) return replace
  if (first === first.toUpperCase() && first !== first.toLowerCase())
    return replace.charAt(0).toUpperCase() + replace.slice(1)
  return replace.charAt(0).toLowerCase() + replace.slice(1)
}

/** Where `find` stands in `text` as a whole word (any case), each with what goes in instead. */
export function wordReplacements(text: string, find: string, replace: string): Replacement[] {
  const needle = find.trim()
  if (!needle) return []
  const pattern = new RegExp(
    `(?<![\\p{L}\\p{N}_])${escapeRegExp(needle)}(?![\\p{L}\\p{N}_])`,
    'giu',
  )
  const out: Replacement[] = []
  for (const match of text.matchAll(pattern)) {
    out.push({
      from: match.index,
      to: match.index + match[0].length,
      text: withCapitalisationOf(match[0], replace),
    })
  }
  return out
}

/** `text` with every straight quote made typographic. `before` is the character just before the
 *  text, when it continues an earlier run (the previous text node of the same paragraph). */
export function typographicQuotes(text: string, before = ''): string {
  let out = ''
  for (let i = 0; i < text.length; i += 1) {
    const ch = text.charAt(i)
    const prev = i === 0 ? before.slice(-1) : text.charAt(i - 1)
    const next = text.charAt(i + 1)
    if (ch === '"') out += opensAfter(prev) ? '“' : '”'
    else if (ch === "'") {
      if (isLetterOrDigit(prev)) out += '’'
      else if (!isWordChar(prev) && isWordChar(next)) out += '‘'
      else out += '’'
    } else out += ch
  }
  return out
}

/** The runs of characters that differ between two strings of one length. */
export function characterDiffs(from: string, to: string): Replacement[] {
  const out: Replacement[] = []
  let i = 0
  while (i < from.length) {
    if (from.charAt(i) === to.charAt(i)) {
      i += 1
      continue
    }
    const start = i
    while (i < from.length && from.charAt(i) !== to.charAt(i)) i += 1
    out.push({ from: start, to: i, text: to.slice(start, i) })
  }
  return out
}

/** What a fix changes in one run of text; nothing for a mode this editor does not know. */
export function fixReplacements(text: string, fix: Fix, before = ''): Replacement[] {
  if (fix.mode === 'word') return wordReplacements(text, fix.find, fix.replace)
  if (fix.mode === 'quotes') return characterDiffs(text, typographicQuotes(text, before))
  return []
}

/** The text as the fix leaves it; for a test and a preview. */
export function fixedText(text: string, fix: Fix): string {
  let out = text
  for (const r of fixReplacements(text, fix).sort((a, b) => b.from - a.from))
    out = out.slice(0, r.from) + r.text + out.slice(r.to)
  return out
}

/**
 * The fix over a piece's editor: every text node is read, the replacements are collected, and
 * one transaction puts them in from the end backwards so earlier positions stay true and each
 * text node keeps its marks. True when something changed. Dispatched through the view, so the
 * editor's own update reaches the page and the usual save follows.
 */
export function applyFix(editor: Editor, fix: Fix): boolean {
  const { state } = editor
  const edits: Replacement[] = []
  let lastParent: unknown = null
  let before = ''
  state.doc.descendants((node, pos, parent) => {
    if (!node.isText) return true
    const text = node.text ?? ''
    const continues = parent !== null && parent === lastParent
    for (const r of fixReplacements(text, fix, continues ? before : ''))
      edits.push({ from: pos + r.from, to: pos + r.to, text: r.text })
    lastParent = parent
    before = text
    return true
  })
  if (edits.length === 0) return false
  const tr = state.tr
  for (const edit of edits.sort((a, b) => b.from - a.from))
    tr.insertText(edit.text, edit.from, edit.to)
  editor.view.dispatch(tr)
  return true
}
