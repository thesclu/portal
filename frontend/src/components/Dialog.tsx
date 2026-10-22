import { useEffect, useRef, type ReactNode } from 'react'

import { CloseIcon } from './icons'
import { cx } from './ui'

/**
 * A modal over the native <dialog>, which gives the focus trap, Esc and the scrim for free.
 * Closing by Esc or by the scrim calls `onClose`; nothing is ever saved by closing.
 *
 * Design 056: 460 px, 18 px radius, the dialog shadow, 110 px from the top (never vertically
 * centred); the title 18 px Libre Franklin 800 with an optional 12.5 px muted subtitle and the
 * × at the right, no header rule; body 18px 22px 20px with a 14 px gap; the footer 16px 24px 20px.
 */
export function Dialog({
  title,
  subtitle,
  open,
  onClose,
  children,
  footer,
  width = 460,
  top = 110,
  bare = false,
  look = 'default',
  centered = false,
  frameless = false,
}: {
  title: string
  subtitle?: ReactNode
  open: boolean
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
  width?: number
  /** Distance from the top of the window. */
  top?: number
  /** No title row and no body padding: the caller draws everything (the quick-jump palette). */
  bare?: boolean
  /** The Inbox's dialogs (159): #fdfdfd, 16 px round, a 17 px title and no ×, the footer on a
   *  grey band under a hairline, 32 px buttons. */
  look?: 'default' | 'inbox'
  /** In the middle of the window, as the Inbox's flows draw them (125, 130), not 110 px down. */
  centered?: boolean
  /** With `bare`: no card of the dialog's own, for content that draws its own (the composer's
   *  "Send this message?", 150), so the two cards never stack. */
  frameless?: boolean
}) {
  const ref = useRef<HTMLDialogElement>(null)

  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    if (open && !dialog.open) {
      dialog.showModal()
      // showModal moves the focus to the first control (the ×); the field a screen draws with
      // the focus (071 "New", 165 "Name") takes it back.
      dialog.querySelector<HTMLElement>('[data-autofocus]')?.focus()
    }
    if (!open && dialog.open) dialog.close()
  }, [open])

  return (
    <dialog
      ref={ref}
      aria-label={bare ? title : undefined}
      onClose={onClose}
      onClick={(event) => {
        if (event.target === ref.current) onClose()
      }}
      style={centered ? { width } : { width, marginTop: top }}
      className={cx(
        centered ? 'm-auto' : 'mx-auto mb-auto',
        'max-w-[calc(100vw-2rem)] overflow-hidden p-0 text-ink outline-none backdrop:scrim',
        frameless
          ? 'bg-transparent shadow-none'
          : look === 'inbox'
            ? 'rounded-16 bg-card shadow-popover-lg'
            : 'rounded-18 bg-card-white shadow-dialog',
      )}
    >
      {open ? (
        bare ? (
          children
        ) : look === 'inbox' ? (
          <div className="flex max-h-[calc(100vh-140px)] flex-col">
            <div className="px-[22px] pt-[18px] pb-1">
              <h2 className="font-display text-lg leading-[normal] font-extrabold text-ink">
                {title}
              </h2>
              {subtitle ? <p className="mt-1 text-125 text-muted">{subtitle}</p> : null}
            </div>
            <div className="flex flex-col gap-[13px] overflow-y-auto px-[22px] pt-3.5 pb-[18px]">
              {children}
            </div>
            {footer ? (
              <div className="flex items-center justify-end gap-2 border-t border-hairline bg-row-alt px-[22px] py-3">
                {footer}
              </div>
            ) : null}
          </div>
        ) : (
          <div className="flex max-h-[calc(100vh-140px)] flex-col">
            <div className="flex items-start gap-3 px-[22px] pt-5">
              <div className="min-w-0 flex-1">
                <h2 className="font-display text-18 leading-[normal] font-extrabold tracking-dialog text-ink">
                  {title}
                </h2>
                {subtitle ? <p className="mt-[3px] text-125 text-muted">{subtitle}</p> : null}
              </div>
              {/* Every dialog draws the 14 px × flush with the title's top and the header's right
                  padding (097 and the 52 others); the 20 px button is pulled out by 3 px on each
                  side so the icon sits exactly there and the target stays 20 px. */}
              <button
                type="button"
                aria-label="Close"
                onClick={onClose}
                className="-m-[3px] inline-flex size-5 shrink-0 items-center justify-center rounded-6 text-muted hover:text-ink"
              >
                <CloseIcon size={14} width={2.2} />
              </button>
            </div>
            <div className="flex flex-col gap-3.5 overflow-y-auto px-[22px] pt-[18px] pb-5">
              {children}
            </div>
            {footer ? (
              <div className="flex items-center justify-end gap-2.5 px-6 pt-4 pb-5">{footer}</div>
            ) : null}
          </div>
        )
      ) : null}
    </dialog>
  )
}
