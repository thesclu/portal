/** A saved revision as lines of text, and the difference between two of them. */

export interface DiffLine {
  kind: 'same' | 'added' | 'removed'
  text: string
}

const META: Array<[key: string, label: string]> = [
  ['template_name', 'Name'],
  ['legal_basis', 'Law'],
  ['jurisdiction', 'Jurisdiction'],
]

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

const str = (value: unknown) => (typeof value === 'string' ? value : '')

/**
 * What a revision says, one line per line of the letter, in the order the letter prints it.
 * An item's own-deadline provision is not printed in a letter but is part of the template, so it
 * gets a line of its own under the item.
 */
export function revisionLines(content: Record<string, unknown>): string[] {
  const lines: string[] = []
  const meta = isRecord(content.meta) ? content.meta : {}
  for (const [key, label] of META) {
    if (str(meta[key])) lines.push(`${label}: ${str(meta[key])}`)
  }
  const sections = Array.isArray(content.sections) ? content.sections : []
  for (const section of sections) {
    if (!isRecord(section)) continue
    const kind = str(section.kind)
    const label = str(section.label)
    const body = str(section.body)
    const text =
      kind === 'item' ? `${label}. ${body}` : kind === 'citation' ? `[${label}] ${body}` : body
    if (lines.length > 0) lines.push('')
    lines.push(...text.split('\n'))
    if (kind === 'item' && str(section.provision)) {
      lines.push(`Own deadline under: ${str(section.provision)}`)
    }
  }
  return lines
}

/** A line-by-line comparison: the longest run of lines both share, and what each side adds. */
export function diffLines(before: string[], after: string[]): DiffLine[] {
  const rows = before.length
  const columns = after.length
  // shared[i][j]: how many lines before[i:] and after[j:] have in common, in order.
  const shared: number[][] = Array.from({ length: rows + 1 }, () =>
    Array<number>(columns + 1).fill(0),
  )
  for (let i = rows - 1; i >= 0; i--) {
    for (let j = columns - 1; j >= 0; j--) {
      shared[i][j] =
        before[i] === after[j]
          ? shared[i + 1][j + 1] + 1
          : Math.max(shared[i + 1][j], shared[i][j + 1])
    }
  }
  const out: DiffLine[] = []
  let i = 0
  let j = 0
  while (i < rows && j < columns) {
    if (before[i] === after[j]) {
      out.push({ kind: 'same', text: before[i] })
      i++
      j++
    } else if (shared[i + 1][j] >= shared[i][j + 1]) {
      out.push({ kind: 'removed', text: before[i++] })
    } else {
      out.push({ kind: 'added', text: after[j++] })
    }
  }
  while (i < rows) out.push({ kind: 'removed', text: before[i++] })
  while (j < columns) out.push({ kind: 'added', text: after[j++] })
  return out
}
