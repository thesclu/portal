import { useEffect, useId, useRef, useState, type ReactNode } from 'react'

import { cx } from './ui'

/**
 * A menu (design 022): 10 px radius, 1 px line, the menu shadow; rows 7px 14px at 12.5 px with
 * a full-width hover fill; 1 px hairline separators with 4 px margins; danger rows in red.
 * `MenuPanel` is the list on its own, for a caller that places it; `Menu` adds the trigger and
 * the open/close.
 */
export interface MenuItem {
  label: string
  onPick: () => void
  danger?: boolean
  disabled?: boolean
  /** A hairline above this row. */
  separator?: boolean
  /** A small-capitals group label above this row (068 "Log a response"). */
  heading?: string
  /** Muted words at the row's right (069 the "E" of Edit request). */
  hint?: string
  /** The row the screen draws lit: the current choice (108 the primary next step, 156 the sort). */
  current?: boolean
  /** A second, smaller line under the label (135 "Admins only · Ask a teammate"). */
  sub?: ReactNode
  /** Drawn at the row's right instead of a hint: a chevron to a submenu (134), a lock (135). */
  trailing?: ReactNode
  /** Greyed like a disabled row but still a button (135: the locked door asks a teammate). */
  muted?: boolean
}

export function MenuPanel({
  items,
  onPicked,
  width = 230,
  className,
  tail = false,
  ...rest
}: {
  items: MenuItem[]
  onPicked?: () => void
  width?: number
  className?: string
  /** The Inbox's menus end in an empty 14 px row (107, 108, 134). */
  tail?: boolean
  id?: string
  'aria-label'?: string
}) {
  return (
    <div
      role="menu"
      {...rest}
      // The screens' menus are content-box: `width` is inside the 1 px border.
      style={{ width, boxSizing: 'content-box' }}
      className={cx(
        'overflow-hidden rounded-10 border border-line bg-card-white py-0 shadow-menu',
        className,
      )}
    >
      {items.map((item, index) => (
        <div key={item.label}>
          {item.separator && index > 0 ? <div className="my-1 h-px bg-hairline" /> : null}
          {item.heading ? (
            <div className="px-3.5 pt-2 pb-1 text-105 font-semibold tracking-label text-muted uppercase">
              {item.heading}
            </div>
          ) : null}
          <button
            type="button"
            role="menuitem"
            disabled={item.disabled}
            onClick={() => {
              onPicked?.()
              item.onPick()
            }}
            className={cx(
              'flex w-full px-3.5 py-[7px] text-left text-125 leading-[normal] hover:bg-hover hover:font-medium',
              'disabled:cursor-not-allowed disabled:text-muted-2 disabled:hover:bg-transparent disabled:hover:font-normal',
              item.sub ? 'items-start' : 'items-center',
              item.current && 'bg-hover font-medium',
              item.danger
                ? 'text-danger'
                : item.muted
                  ? 'text-muted-2 hover:font-normal'
                  : 'text-ink',
            )}
          >
            {item.sub ? (
              // 135 draws the lock on its own line under the words.
              <span className="min-w-0 flex-1">
                <span className="block">{item.label}</span>
                <span className="mt-0.5 block text-115 font-normal text-muted">{item.sub}</span>
                {item.trailing ? <span className="mt-[3px] block">{item.trailing}</span> : null}
              </span>
            ) : item.trailing ? (
              // 134 draws the submenu's chevron right after the words.
              <span className="flex-1">
                {item.label}
                <span className="ml-0.5 inline-block align-middle">{item.trailing}</span>
              </span>
            ) : (
              <span className="flex-1">{item.label}</span>
            )}
            {item.hint ? (
              <span className="text-11 font-normal text-muted-2">{item.hint}</span>
            ) : null}
          </button>
        </div>
      ))}
      {tail ? <div aria-hidden className="h-3.5" /> : null}
    </div>
  )
}

export function Menu({
  label,
  items,
  align = 'right',
  width,
  className,
  panelClassName,
  children,
  buttonClassName,
}: {
  /** What the trigger is for, for assistive technology. */
  label: string
  items: MenuItem[]
  align?: 'left' | 'right'
  width?: number
  className?: string
  /** Extra classes for the open list (a long list scrolls: `max-h-… overflow-y-auto`). */
  panelClassName?: string
  /** The trigger's content (given whether the menu is open, for a pressed look); the trigger
   *  itself is drawn here. */
  children: ReactNode | ((open: boolean) => ReactNode)
  /** Extra classes for the trigger button. */
  buttonClassName?: string
}) {
  const [open, setOpen] = useState(false)
  const id = useId()
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    function away(event: PointerEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false)
    }
    function key(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', away)
    document.addEventListener('keydown', key)
    return () => {
      document.removeEventListener('pointerdown', away)
      document.removeEventListener('keydown', key)
    }
  }, [open])

  return (
    <div ref={ref} className={cx('relative inline-flex', className)}>
      <button
        type="button"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((was) => !was)}
        className={cx(
          'inline-flex items-center rounded-8 outline-none focus-visible:outline-2 focus-visible:outline-brand',
          buttonClassName,
        )}
      >
        {typeof children === 'function' ? children(open) : children}
      </button>
      {open ? (
        <MenuPanel
          id={id}
          aria-label={label}
          items={items}
          width={width}
          onPicked={() => setOpen(false)}
          className={cx(
            'absolute top-full z-20 mt-1',
            align === 'right' ? 'right-0' : 'left-0',
            panelClassName,
          )}
        />
      ) : null}
    </div>
  )
}
