/** Templates · Shared blocks · Letterheads · Saved replies: the parts of the Templates destination
 *  (saved replies moved here from Settings, DECISIONS 42h). */
import { Link, useLocation } from 'react-router'

import { cx } from '../../components/ui'

export const HOME = '/templates'
export const BLOCKS = `${HOME}/blocks`
export const LETTERHEADS = `${HOME}/letterheads`
export const REPLIES = `${HOME}/replies`

const SECTIONS = [
  { to: HOME, label: 'Templates' },
  { to: BLOCKS, label: 'Shared blocks' },
  { to: LETTERHEADS, label: 'Letterheads' },
  { to: REPLIES, label: 'Saved replies' },
]

export function LibraryNav() {
  const { pathname } = useLocation()
  const here = pathname.replace(/\/+$/, '')
  const current =
    SECTIONS.slice(1).find((s) => here === s.to || here.startsWith(`${s.to}/`)) ?? SECTIONS[0]
  return (
    <nav aria-label="Letter templates" className="mb-5 flex gap-1 border-b border-line">
      {SECTIONS.map((section) => (
        <Link
          key={section.to}
          to={section.to}
          aria-current={section === current ? 'page' : undefined}
          className={cx(
            '-mb-px border-b-2 px-3 py-2 text-13',
            section === current
              ? 'border-ink font-semibold text-ink'
              : 'border-transparent text-ink-2',
          )}
        >
          {section.label}
        </Link>
      ))}
    </nav>
  )
}

/** The column heads of the library's tables, in the design's small capitals. */
export function Th({ children, className }: { children?: React.ReactNode; className?: string }) {
  return (
    <th
      className={cx(
        'py-2 pr-3 text-left text-105 font-semibold tracking-[0.08em] text-ink-3 uppercase',
        className,
      )}
    >
      {children}
    </th>
  )
}

/** "⋯" at the end of a row: the row's actions, as the design draws them. */
export const ROW_MENU_BUTTON = 'h-7 min-w-7 rounded-9 text-ink-2 hover:bg-page hover:text-ink'
