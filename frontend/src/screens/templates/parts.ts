/**
 * The Parts list of a template (design 1z): its pieces and its shared blocks as one ordered
 * list. The letter orders both by one `sort_order` sequence; pieces are numbered 0, 1, 2… by
 * their position when saved, and a block's `sort_order` k puts it before the piece at k (a
 * block wins a tie), or after every piece when k is past the last one. So a block's place is
 * "how many pieces come before it", and that is what is stored for it here.
 */

export type PartRow = { kind: 'piece'; key: string } | { kind: 'block'; id: string }

/** A shared block's place: the number of pieces printed before it. */
export interface Placement {
  id: string
  position: number
}

export const rowKey = (row: PartRow) =>
  row.kind === 'piece' ? `piece:${row.key}` : `block:${row.id}`

/** The one list, pieces in their order with each block before the piece its position names. */
export function partRows(pieceKeys: string[], blocks: Placement[]): PartRow[] {
  const rows: PartRow[] = []
  const at = (position: number) =>
    blocks.filter((b) => b.position === position).map((b): PartRow => ({ kind: 'block', id: b.id }))
  pieceKeys.forEach((key, index) => {
    rows.push(...at(index))
    rows.push({ kind: 'piece', key })
  })
  rows.push(
    ...blocks
      .filter((b) => b.position >= pieceKeys.length)
      .map((b): PartRow => ({ kind: 'block', id: b.id })),
  )
  return rows
}

/** Read back from a list: the pieces' order and each block's place. */
export function partsOf(rows: PartRow[]): { pieceKeys: string[]; blocks: Placement[] } {
  const pieceKeys: string[] = []
  const blocks: Placement[] = []
  for (const row of rows) {
    if (row.kind === 'piece') pieceKeys.push(row.key)
    else blocks.push({ id: row.id, position: pieceKeys.length })
  }
  return { pieceKeys, blocks }
}

/** The list with the row at `from` put at `to`. */
export function moveRow(rows: PartRow[], from: number, to: number): PartRow[] {
  if (from === to || from < 0 || from >= rows.length || to < 0 || to >= rows.length) return rows
  const next = [...rows]
  const [moved] = next.splice(from, 1)
  next.splice(to, 0, moved!)
  return next
}

/** Where a new row goes: right after the selected row, or at the end when nothing is selected. */
export function indexAfter(rows: PartRow[], selected: string | null): number {
  const index = selected === null ? -1 : rows.findIndex((row) => rowKey(row) === selected)
  return index === -1 ? rows.length : index + 1
}

/** How many pieces sit before `index` in the list: the piece index a piece put there gets. */
export function piecesBefore(rows: PartRow[], index: number): number {
  return rows.slice(0, index).filter((row) => row.kind === 'piece').length
}

/** What the blocks API is given so the letter prints the blocks where the list has them. */
export function blocksPayload(blocks: Placement[]): { block_ids: string[]; sort_orders: number[] } {
  return { block_ids: blocks.map((b) => b.id), sort_orders: blocks.map((b) => b.position) }
}

export function samePlacements(a: Placement[], b: Placement[]): boolean {
  return (
    a.length === b.length && a.every((x, i) => x.id === b[i]!.id && x.position === b[i]!.position)
  )
}
