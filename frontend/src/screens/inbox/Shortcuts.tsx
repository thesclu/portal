/**
 * The Inbox's keyboard (ARCHITECTURE §2.5, design 123): the sheet "?" opens, and the keys
 * themselves. A key does nothing while the person is typing in a field or a dialog is open.
 */
import { useEffect, useRef } from 'react'

import { Dialog } from '../../components/Dialog'
import { CloseIcon } from '../../components/icons'

const SECTIONS: Array<{ title: string; rows: Array<[string, string[]]> }> = [
  {
    title: 'Moving around',
    rows: [
      ['Next message', ['J']],
      ['Previous message', ['K']],
      ['Open', ['↵']],
      ['Close or go back', ['Esc']],
      ['Search everything', ['⌘', 'K']],
    ],
  },
  {
    title: 'Acting',
    rows: [
      ['Mark reviewed', ['E']],
      ['Reviewed and open next', ['⇧', 'E']],
      ['Attach to a request', ['A']],
      ['Reply', ['R']],
      ['Forward', ['F']],
    ],
  },
  {
    title: 'Going to',
    rows: [
      ['Inbox', ['G', 'I']],
      ['Flagged', ['G', 'F']],
      ['Tasks', ['G', 'T']],
      ['Docket', ['G', 'D']],
    ],
  },
  {
    title: 'Writing',
    rows: [
      ['Send', ['⌘', '↵']],
      ['Saved replies', ['⌘', '⇧', 'S']],
      ['Write a note', ['N']],
      ['This list', ['?']],
    ],
  },
]

function Key({ children }: { children: string }) {
  return (
    <span className="box-border inline-flex h-[22px] min-w-[22px] items-center justify-center rounded-kbd border border-b-2 border-line-2 bg-surface-row px-1.5 text-115 font-medium text-ink">
      {children}
    </span>
  )
}

/** The sheet (123): two columns of what each key does. */
export function ShortcutSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Dialog title="Keyboard shortcuts" open={open} onClose={onClose} bare centered width={640}>
      <div className="flex items-center border-b border-hairline px-6 py-[18px]">
        <span className="font-display text-18 font-extrabold tracking-dialog text-ink">
          Keyboard shortcuts
        </span>
        <button
          type="button"
          aria-label="Close"
          onClick={onClose}
          className="ml-auto inline-flex size-5 items-center justify-center rounded-6 hover:bg-hover"
        >
          <CloseIcon size={14} width={2} stroke="var(--color-ink-3)" />
        </button>
      </div>
      <div className="grid grid-cols-2 gap-x-[34px] gap-y-[18px] px-6 pt-[18px] pb-[22px]">
        {SECTIONS.map((section) => (
          <div key={section.title}>
            <div className="mb-1 text-105 font-semibold tracking-group text-muted uppercase">
              {section.title}
            </div>
            {section.rows.map(([label, keys]) => (
              <div key={label} className="flex items-center gap-2.5 py-1.5">
                <span className="flex-1 text-125 text-ink-2">{label}</span>
                <span className="inline-flex gap-1">
                  {keys.map((key) => (
                    <Key key={key}>{key}</Key>
                  ))}
                </span>
              </div>
            ))}
          </div>
        ))}
      </div>
    </Dialog>
  )
}

export interface InboxKeys {
  next: () => void
  previous: () => void
  open: () => void
  close: () => void
  select: () => void
  reviewed: (andNext: boolean) => void
  attach: () => void
  reply: () => void
  forward: () => void
  note: () => void
  help: () => void
  search: () => void
  go: (where: 'inbox' | 'flagged' | 'tasks' | 'docket') => void
}

const typing = (target: EventTarget | null) => {
  const element = target as HTMLElement | null
  if (!element) return false
  return /^(INPUT|TEXTAREA|SELECT)$/.test(element.tagName) || element.isContentEditable
}

/** The Inbox's single-key commands. "G" then a letter within a second goes somewhere. */
export function useInboxKeys(keys: InboxKeys, enabled: boolean): void {
  const ref = useRef(keys)
  useEffect(() => {
    ref.current = keys
  })
  useEffect(() => {
    if (!enabled) return
    let pendingGo = 0
    function onKey(event: KeyboardEvent) {
      if (event.metaKey || event.ctrlKey || event.altKey) return
      if (typing(event.target) || document.querySelector('dialog[open]')) return
      const k = ref.current
      const key = event.key
      if (pendingGo && Date.now() - pendingGo < 1000) {
        pendingGo = 0
        const where = { i: 'inbox', f: 'flagged', t: 'tasks', d: 'docket' }[key.toLowerCase()] as
          'inbox' | 'flagged' | 'tasks' | 'docket' | undefined
        if (where) {
          event.preventDefault()
          k.go(where)
        }
        return
      }
      const run = (action: () => void) => {
        event.preventDefault()
        action()
      }
      switch (key) {
        case 'j':
          return run(k.next)
        case 'k':
          return run(k.previous)
        case 'Enter':
        case 'o':
          return run(k.open)
        case 'Escape':
          return k.close()
        case 'x':
          return run(k.select)
        case 'e':
          return run(() => k.reviewed(false))
        case 'E':
          return run(() => k.reviewed(true))
        case 'a':
          return run(k.attach)
        case 'r':
          return run(k.reply)
        case 'f':
          return run(k.forward)
        case 'n':
          return run(k.note)
        case '?':
          return run(k.help)
        case '/':
          return run(k.search)
        case 'g':
          pendingGo = Date.now()
          return
        default:
          return
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [enabled])
}
