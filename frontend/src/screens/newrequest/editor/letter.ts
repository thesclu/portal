/**
 * The pure part of the letter editor (P19c): the editor's own picture of a draft's pieces and
 * citations, the numbering of items, the chips a plain text's `{{NAME}}` tokens become, and the
 * body of the one PUT that saves it all. No React, no network, so every rule here is testable.
 */
import type { JSONContent } from '@tiptap/core'

import type { Schemas } from '../../../api/client'

export const PLACEHOLDER_NAMES = [
  'AGENCY_FULL',
  'AGENCY_ABBR',
  'RECIPIENT_OFFICE',
  'ADDRESS',
  'AGENCY_STREETADDRESS',
  'AGENCY_CITYSTATEZIP',
  'CHANNEL_METHOD',
  'FILING_DATE',
] as const
export type PlaceholderName = (typeof PLACEHOLDER_NAMES)[number]

export type PieceKind = 'item' | 'prose'

/** A piece as the editor holds it. `key` is the editor's own handle, stable across saves; `id`
 *  is the server's and is null until the first save that creates the row. */
export interface LocalPiece {
  key: string
  id: string | null
  kind: PieceKind
  item_number: string
  parent_key: string | null
  provision: string | null
  doc: JSONContent
}

export interface LocalCitation {
  key: string
  id: string | null
  marker: string
  citation_text: string
  item_key: string | null
}

let counter = 0
/** A key for a piece or a citation made in this session. */
export function localKey(): string {
  counter += 1
  return `local-${counter}`
}

// ---- plain text with {{NAME}} tokens, as a document --------------------------------------------

const TOKEN = /\{\{([^{}]*)\}\}/g

function isPlaceholderName(name: string): name is PlaceholderName {
  return (PLACEHOLDER_NAMES as readonly string[]).includes(name)
}

/** One line of text as inline nodes: text runs and placeholder chips. A `{{NAME}}` the server
 *  does not know stays as text, exactly as the server leaves it standing. */
export function inlineFromText(line: string): JSONContent[] {
  const out: JSONContent[] = []
  let last = 0
  for (const match of line.matchAll(TOKEN)) {
    const name = match[1] ?? ''
    const start = match.index ?? 0
    if (!isPlaceholderName(name)) continue
    if (start > last) out.push({ type: 'text', text: line.slice(last, start) })
    out.push({ type: 'placeholder', attrs: { name } })
    last = start + match[0].length
  }
  if (last < line.length) out.push({ type: 'text', text: line.slice(last) })
  return out
}

/** Plain text as a document: a blank line separates paragraphs, a single newline is a hard
 *  break, as the server's own text-to-page rendering reads it. */
export function docFromText(text: string): JSONContent {
  const paragraphs = text
    .replace(/\r\n/g, '\n')
    .split(/\n{2,}/)
    .filter((block) => block.trim() !== '')
  if (paragraphs.length === 0) return { type: 'doc', content: [{ type: 'paragraph' }] }
  return {
    type: 'doc',
    content: paragraphs.map((block) => {
      const content: JSONContent[] = []
      block.split('\n').forEach((line, index) => {
        if (index > 0) content.push({ type: 'hardBreak' })
        content.push(...inlineFromText(line))
      })
      return content.length ? { type: 'paragraph', content } : { type: 'paragraph' }
    }),
  }
}

export function docOf(rich: Record<string, unknown> | null | undefined, body: string): JSONContent {
  if (rich && rich.type === 'doc') return rich as JSONContent
  return docFromText(body)
}

// ---- from the server's shape -------------------------------------------------------------------

export function localPieces(pieces: Schemas['PieceOut'][]): LocalPiece[] {
  const sorted = [...pieces].sort((a, b) => a.sort_order - b.sort_order)
  const keyOf = new Map(sorted.map((p) => [p.id, localKey()]))
  return sorted.map((p) => ({
    key: keyOf.get(p.id)!,
    id: p.id,
    kind: p.kind === 'prose' ? 'prose' : 'item',
    item_number: p.item_number,
    parent_key: p.parent_item_id ? (keyOf.get(p.parent_item_id) ?? null) : null,
    provision: p.provision,
    doc: docOf(p.body_rich, p.body),
  }))
}

export function localCitations(
  citations: Schemas['LetterCitationOut'][],
  pieces: LocalPiece[],
): LocalCitation[] {
  const keyOf = new Map(pieces.filter((p) => p.id).map((p) => [p.id!, p.key]))
  return [...citations]
    .sort((a, b) => a.sort_order - b.sort_order)
    .map((c) => ({
      key: localKey(),
      id: c.id,
      marker: c.marker,
      citation_text: c.citation_text,
      item_key: c.item_id ? (keyOf.get(c.item_id) ?? null) : null,
    }))
}

// ---- numbering ---------------------------------------------------------------------------------

/** 1 → a, 26 → z, 27 → aa: how a sub-item is lettered under its parent. */
export function letterOf(n: number): string {
  let out = ''
  let rest = n
  while (rest > 0) {
    rest -= 1
    out = String.fromCharCode(97 + (rest % 26)) + out
    rest = Math.floor(rest / 26)
  }
  return out
}

/** Whether a piece is indented: one level, under a top-level item. */
export function depthOf(piece: LocalPiece): number {
  return piece.parent_key ? 1 : 0
}

/**
 * The order IS the numbering. Top-level items count 1, 2, 3…; a sub-item belongs to the nearest
 * top-level item above it and is lettered a, b, c… under it, so its label is "2a". Prose
 * paragraphs sit anywhere and carry no number. A sub-item with nothing above it is promoted.
 * Pieces that need no change keep their identity.
 */
export function normalize(pieces: LocalPiece[]): LocalPiece[] {
  let top = 0
  let lastTop: LocalPiece | null = null
  const letters = new Map<string, number>()
  return pieces.map((piece) => {
    if (piece.kind !== 'item') return piece
    const parentKey = piece.parent_key === null ? null : (lastTop?.key ?? null)
    let number: string
    if (parentKey === null) {
      top += 1
      number = String(top)
    } else {
      const n = (letters.get(parentKey) ?? 0) + 1
      letters.set(parentKey, n)
      number = `${lastTop!.item_number}${letterOf(n)}`
    }
    const next =
      piece.parent_key === parentKey && piece.item_number === number
        ? piece
        : { ...piece, parent_key: parentKey, item_number: number }
    if (parentKey === null) lastTop = next
    return next
  })
}

/** The number a new item gets: the next top-level number, or the next letter under `parent`. */
export function nextItemNumber(pieces: LocalPiece[], parent: LocalPiece | null): string {
  if (!parent)
    return String(pieces.filter((p) => p.kind === 'item' && p.parent_key === null).length + 1)
  return `${parent.item_number}${letterOf(
    pieces.filter((p) => p.kind === 'item' && p.parent_key === parent.key).length + 1,
  )}`
}

/** The next footnote marker: one past the highest number in use. */
export function nextMarker(citations: Array<{ marker: string }>): string {
  const used = citations.map((c) => Number.parseInt(c.marker, 10)).filter((n) => Number.isFinite(n))
  return String(used.length ? Math.max(...used) + 1 : 1)
}

// ---- edits -------------------------------------------------------------------------------------

export function emptyDoc(): JSONContent {
  return { type: 'doc', content: [{ type: 'paragraph' }] }
}

/** A new piece at `index`. An item added right after a sub-item joins that group. */
export function insertPiece(pieces: LocalPiece[], index: number, kind: PieceKind): LocalPiece[] {
  const at = Math.max(0, Math.min(index, pieces.length))
  const before = at > 0 ? pieces[at - 1] : undefined
  const parent =
    kind === 'item' && before?.kind === 'item' && before.parent_key
      ? (pieces.find((p) => p.key === before.parent_key) ?? null)
      : null
  const piece: LocalPiece = {
    key: localKey(),
    id: null,
    kind,
    item_number: kind === 'item' ? nextItemNumber(pieces, parent) : '',
    parent_key: parent?.key ?? null,
    provision: null,
    doc: emptyDoc(),
  }
  return normalize([...pieces.slice(0, at), piece, ...pieces.slice(at)])
}

export function removePiece(pieces: LocalPiece[], key: string): LocalPiece[] {
  return normalize(pieces.filter((p) => p.key !== key))
}

export function movePiece(pieces: LocalPiece[], from: number, to: number): LocalPiece[] {
  if (from === to || from < 0 || from >= pieces.length || to < 0 || to >= pieces.length)
    return pieces
  const next = [...pieces]
  const [moved] = next.splice(from, 1)
  next.splice(to, 0, moved!)
  return normalize(next)
}

/** "Make sub-item of the previous": under the item before it, or into its group when that one
 *  is a sub-item itself. Unchanged when there is no item above. */
export function demotePiece(pieces: LocalPiece[], key: string): LocalPiece[] {
  const index = pieces.findIndex((p) => p.key === key)
  if (index <= 0 || pieces[index]!.kind !== 'item') return pieces
  const previous = pieces
    .slice(0, index)
    .reverse()
    .find((p) => p.kind === 'item')
  if (!previous) return pieces
  const parentKey = previous.parent_key ?? previous.key
  return normalize(pieces.map((p) => (p.key === key ? { ...p, parent_key: parentKey } : p)))
}

export function promotePiece(pieces: LocalPiece[], key: string): LocalPiece[] {
  return normalize(pieces.map((p) => (p.key === key ? { ...p, parent_key: null } : p)))
}

export function canDemote(pieces: LocalPiece[], key: string): boolean {
  const index = pieces.findIndex((p) => p.key === key)
  if (index <= 0 || pieces[index]!.kind !== 'item' || pieces[index]!.parent_key) return false
  return pieces.slice(0, index).some((p) => p.kind === 'item')
}

// ---- the save ----------------------------------------------------------------------------------

/** A piece as it is sent: never its plain `body`, which the server derives from the document. */
export type PieceSave = Omit<Schemas['PieceIn'], 'body'>
export interface LetterSave {
  subject: string
  pieces: PieceSave[]
  citations: Schemas['LetterCitationIn'][]
}

/**
 * The whole letter in one body: the pieces in order (the server numbers `sort_order` from the
 * position), a new sub-item naming a parent that is new too by position (`parent_index`), and
 * the citations with the item each belongs to when that item already has an id.
 */
export function savePayload(
  subject: string,
  pieces: LocalPiece[],
  citations: LocalCitation[],
): LetterSave {
  const indexOf = new Map(pieces.map((p, i) => [p.key, i]))
  const idOf = (key: string | null) =>
    key === null ? null : (pieces[indexOf.get(key) ?? -1]?.id ?? null)
  return {
    subject,
    pieces: pieces.map((p) => {
      const piece: PieceSave = {
        id: p.id,
        kind: p.kind,
        item_number: p.kind === 'item' ? p.item_number : '',
        provision: p.provision,
        body_rich: p.doc as Record<string, unknown>,
      }
      if (p.parent_key !== null && indexOf.has(p.parent_key)) {
        const parentId = idOf(p.parent_key)
        if (parentId) piece.parent_item_id = parentId
        else piece.parent_index = indexOf.get(p.parent_key)
      }
      return piece
    }),
    citations: citations.map((c) => ({
      id: c.id,
      marker: c.marker,
      citation_text: c.citation_text,
      item_id: idOf(c.item_key),
    })),
  }
}

/** After a save: the ids the server minted, by the position each row was sent at. */
export function withServerIds<T extends { key: string; id: string | null }>(
  local: T[],
  sentKeys: string[],
  saved: Array<{ id: string }>,
): T[] {
  const idByKey = new Map<string, string>()
  sentKeys.forEach((key, index) => {
    const row = saved[index]
    if (row) idByKey.set(key, row.id)
  })
  return local.map((row) =>
    row.id === null && idByKey.has(row.key) ? { ...row, id: idByKey.get(row.key)! } : row,
  )
}

// ---- the header line ---------------------------------------------------------------------------

export function agoLabel(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  if (s < 60) return `saved ${s}s ago`
  if (s < 3600) return `saved ${Math.floor(s / 60)}m ago`
  return `saved ${Math.floor(s / 3600)}h ago`
}

export function lettersLabel(n: number): string {
  return `${n} letter${n === 1 ? '' : 's'}`
}

export function metaLine(parts: Array<string | null | undefined | false>): string {
  return parts.filter((part): part is string => typeof part === 'string' && part !== '').join(' · ')
}

// ---- the document as plain text ----------------------------------------------------------------

function inlineText(node: JSONContent): string {
  switch (node.type) {
    case 'text':
      return node.text ?? ''
    case 'hardBreak':
      return '\n'
    case 'placeholder':
      return `{{${String(node.attrs?.name ?? '')}}}`
    case 'footnoteRef':
      return `[${String(node.attrs?.marker ?? '')}]`
    default:
      return (node.content ?? []).map(inlineText).join('')
  }
}

function listItemText(item: JSONContent): string {
  return (item.content ?? [])
    .map((child) => {
      if (child.type === 'bulletList' || child.type === 'orderedList') {
        const inner: string[] = []
        walkText(child, inner, 1)
        return `\n${inner.join('\n')}`
      }
      return inlineText(child).trim()
    })
    .join('')
}

function walkText(node: JSONContent, blocks: string[], depth: number): void {
  const children = node.content ?? []
  switch (node.type) {
    case 'paragraph':
    case 'heading':
      blocks.push(inlineText(node).trim())
      return
    case 'bulletList':
      for (const child of children) blocks.push(`${'  '.repeat(depth)}- ${listItemText(child)}`)
      return
    case 'orderedList': {
      const start = Number(node.attrs?.start ?? 1) || 1
      children.forEach((child, offset) =>
        blocks.push(`${'  '.repeat(depth)}${start + offset}. ${listItemText(child)}`),
      )
      return
    }
    case 'table':
      blocks.push(
        children
          .map((row) => (row.content ?? []).map((cell) => inlineText(cell).trim()).join(' | '))
          .join('\n'),
      )
      return
    default:
      for (const child of children) walkText(child, blocks, depth)
  }
}

/**
 * The document as the server's own `rich_text` derives it: paragraphs separated by a blank line,
 * list items as "- " or "1. ", table cells joined with " | ", a placeholder as `{{NAME}}` and a
 * footnote reference as `[n]`. Sent beside a document wherever the server does not derive the
 * plain text itself (a shared block, a letterhead's opening and closing).
 */
export function plainText(doc: JSONContent | null | undefined): string {
  if (!doc || typeof doc !== 'object') return ''
  const blocks: string[] = []
  walkText(doc, blocks, 0)
  return blocks.filter((block) => block.trim() !== '').join('\n\n')
}

/** The opening words of a document, for a list that names each part by what it says. */
export function firstWords(doc: JSONContent | null | undefined, limit = 90): string {
  const text = plainText(doc).replace(/\s+/g, ' ').trim()
  if (text.length <= limit) return text
  const cut = text.lastIndexOf(' ', limit)
  return `${text.slice(0, cut > limit / 2 ? cut : limit)}…`
}

/** The pieces in the order `keys` names; a key not found is left out. */
export function reorderPieces(pieces: LocalPiece[], keys: string[]): LocalPiece[] {
  const byKey = new Map(pieces.map((p) => [p.key, p]))
  return normalize(keys.flatMap((key) => byKey.get(key) ?? []))
}
