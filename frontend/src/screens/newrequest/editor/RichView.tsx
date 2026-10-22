/** A document rendered read-only, the way the page shows the letterhead, opening and closing. */
import type { JSONContent } from '@tiptap/core'
import { Fragment, type ReactNode } from 'react'

const MARK_TAGS: Record<string, 'strong' | 'em' | 'u' | 's' | 'code'> = {
  bold: 'strong',
  italic: 'em',
  underline: 'u',
  strike: 's',
  code: 'code',
}

function inline(node: JSONContent, key: number): ReactNode {
  if (node.type === 'text') {
    let out: ReactNode = node.text ?? ''
    for (const mark of node.marks ?? []) {
      const Tag = MARK_TAGS[mark.type]
      if (Tag) out = <Tag>{out}</Tag>
    }
    return <Fragment key={key}>{out}</Fragment>
  }
  if (node.type === 'hardBreak') return <br key={key} />
  if (node.type === 'placeholder')
    return (
      <span key={key} className="chip" data-name={String(node.attrs?.name ?? '')}>
        {String(node.attrs?.name ?? '')}
      </span>
    )
  if (node.type === 'footnoteRef')
    return (
      <sup key={key} className="fn">
        {String(node.attrs?.marker ?? '')}
      </sup>
    )
  return <Fragment key={key}>{(node.content ?? []).map(inline)}</Fragment>
}

function block(node: JSONContent, key: number): ReactNode {
  const children = node.content ?? []
  const inner = () => children.map(block)
  const text = () => children.map(inline)
  switch (node.type) {
    case 'paragraph':
      return <p key={key}>{text()}</p>
    case 'heading': {
      const level = Math.min(Math.max(Number(node.attrs?.level ?? 2), 1), 4)
      const Tag = `h${level}` as 'h1' | 'h2' | 'h3' | 'h4'
      return <Tag key={key}>{text()}</Tag>
    }
    case 'bulletList':
      return <ul key={key}>{inner()}</ul>
    case 'orderedList':
      return (
        <ol key={key} start={Number(node.attrs?.start ?? 1)}>
          {inner()}
        </ol>
      )
    case 'listItem':
      return <li key={key}>{inner()}</li>
    case 'blockquote':
      return <blockquote key={key}>{inner()}</blockquote>
    case 'horizontalRule':
      return <hr key={key} />
    case 'table':
      return (
        <table key={key} className="grid">
          <tbody>{inner()}</tbody>
        </table>
      )
    case 'tableRow':
      return <tr key={key}>{inner()}</tr>
    case 'tableHeader':
      return <th key={key}>{inner()}</th>
    case 'tableCell':
      return <td key={key}>{inner()}</td>
    case 'text':
    case 'hardBreak':
    case 'placeholder':
    case 'footnoteRef':
      return inline(node, key)
    default:
      return <Fragment key={key}>{inner()}</Fragment>
  }
}

export function RichView({ doc }: { doc: JSONContent | null | undefined }) {
  if (!doc) return null
  return <>{(doc.content ?? []).map(block)}</>
}
