/**
 * "Preview as ▾" in stage 2's letter header (045–047): the muted words and a 9 px chevron; with an
 * agency chosen, its name in ink, underlined in the check grey, and a × that goes back to the
 * placeholders (046). The menu (047): 240 px, 10 px round, flush 12.5 px rows, the chosen one on
 * the hover grey with a brand tick; it opens and closes with an empty 14 px row, as drawn.
 */
import { useEffect, useRef, useState } from 'react'

import { CheckIcon, ChevronDown, CloseIcon } from '../../../components/icons'
import { cx } from '../../../components/ui'

export interface PreviewChoice {
  id: string
  name: string
}

export function PreviewAs({
  choices,
  value,
  onChange,
}: {
  choices: PreviewChoice[]
  value: string
  onChange: (id: string) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLSpanElement>(null)
  useEffect(() => {
    if (!open) return
    const away = (event: PointerEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false)
    }
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', away)
    document.addEventListener('keydown', key)
    return () => {
      document.removeEventListener('pointerdown', away)
      document.removeEventListener('keydown', key)
    }
  }, [open])
  const chosen = choices.find((c) => c.id === value) ?? null
  return (
    <span
      ref={ref}
      className="relative inline-flex shrink-0 items-baseline gap-1.5 text-125 leading-[normal] whitespace-nowrap"
    >
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={choices.length === 0}
        title={choices.length === 0 ? 'Choose agencies first' : undefined}
        onClick={() => setOpen((was) => !was)}
        className="inline-flex items-baseline gap-1.5"
      >
        <span className="text-muted">Preview as</span>
        {chosen ? (
          <span className="font-medium text-ink underline decoration-line-control underline-offset-[3px]">
            {chosen.name}
          </span>
        ) : (
          <ChevronDown size={9} width={2.6} stroke="var(--color-muted)" className="self-center" />
        )}
      </button>
      {chosen ? (
        <button
          type="button"
          aria-label="Back to the placeholders"
          title="Back to the placeholders"
          onClick={() => onChange('')}
          className="ml-0.5 inline-flex self-center"
        >
          <CloseIcon size={11} width={2.4} stroke="var(--color-muted)" />
        </button>
      ) : null}
      {open ? (
        <div
          role="menu"
          aria-label="Preview as"
          className="absolute top-[calc(100%+6px)] left-0 z-[5] box-content w-[240px] overflow-hidden rounded-10 border border-line bg-card-white shadow-menu"
        >
          <div aria-hidden className="h-3.5" />
          {choices.map((choice) => {
            const on = choice.id === value
            return (
              <button
                key={choice.id}
                type="button"
                role="menuitemradio"
                aria-checked={on}
                onClick={() => {
                  onChange(choice.id)
                  setOpen(false)
                }}
                className={cx(
                  'flex w-full items-center px-3.5 py-[7px] text-left text-125 leading-[normal] text-ink hover:bg-hover',
                  on ? 'bg-hover font-medium' : 'font-normal',
                )}
              >
                <span className="min-w-0 flex-1 truncate">{choice.name}</span>
                {on ? <CheckIcon size={12} width={2.6} stroke="var(--color-brand)" /> : null}
              </button>
            )
          })}
          <div aria-hidden className="h-3.5" />
        </div>
      ) : null}
    </span>
  )
}
