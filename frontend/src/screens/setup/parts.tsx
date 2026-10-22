import type { ReactNode } from 'react'

import { cx } from '../../components/ui'

/**
 * The frame every setup screen is drawn in (006–012): the page grey with no sidebar, the 64 px
 * header (the letters logo at the left, "Step N of M" at the right), the 420 px progress bar 36
 * px under it (a 40 px space on Done, which has none), and the 520 px card 56 px lower, 18 px
 * round, raised by the setup card's shadow, 34/36/28 px inside with 22 px between its parts.
 */
export function SetupFrame({
  label,
  progress,
  logoAlt,
  cardClassName,
  children,
}: {
  label: string
  progress: { index: number; total: number } | null
  logoAlt: string
  cardClassName?: string
  children: ReactNode
}) {
  return (
    <div className="flex min-h-full flex-col items-center bg-page pb-14 leading-[normal]">
      <header className="box-border flex h-16 w-full items-center justify-between px-7">
        <img src="/brand/sclu-letters-blue.png" alt={logoAlt} className="h-5 w-auto" />
        <span className="text-125 text-muted">{label}</span>
      </header>
      {progress ? (
        <div
          role="progressbar"
          aria-label="Setup"
          aria-valuemin={1}
          aria-valuemax={progress.total}
          aria-valuenow={progress.index + 1}
          className="mt-9 flex w-[420px] gap-1.5"
        >
          {Array.from({ length: progress.total }, (_, at) => (
            <span
              key={at}
              className={cx(
                'h-1 flex-1 rounded-pill',
                at <= progress.index ? 'bg-brand' : 'bg-track',
              )}
            />
          ))}
        </div>
      ) : (
        <div aria-hidden className="h-10" />
      )}
      <main
        className={cx(
          'mt-14 box-border flex w-[520px] flex-col gap-[22px] rounded-18 bg-card-white px-9 pt-[34px] pb-7 shadow-setup-card',
          cardClassName,
        )}
      >
        {children}
      </main>
    </div>
  )
}

/** The card's title, 24 px Libre Franklin 800, with the 13.5 px line under it when there is
 *  one (006, 008). */
export function SetupTitle({ title, sub }: { title: string; sub?: ReactNode }) {
  const heading = (
    <h1 className="font-display text-24 font-extrabold tracking-title text-ink">{title}</h1>
  )
  if (!sub) return heading
  return (
    <div className="flex flex-col gap-1.5">
      {heading}
      <p className="text-135 leading-[1.5] text-ink-2">{sub}</p>
    </div>
  )
}

/** The foot of the card: the quiet Back (Not you? on the welcome) and the 9 px brand square at
 *  the right; an error from the server sits above them. */
export function SetupActions({
  backLabel = 'Back',
  onBack,
  nextLabel,
  onNext,
  disabled = false,
  busy = false,
  error,
  hint,
}: {
  backLabel?: string
  onBack?: () => void
  nextLabel: string
  onNext: () => void
  disabled?: boolean
  busy?: boolean
  error?: string | null
  /** Said to a screen reader (and on hover) while the button waits for something. */
  hint?: string
}) {
  return (
    <>
      {error ? (
        <p role="alert" className="-mb-2 text-125 text-danger">
          {error}
        </p>
      ) : null}
      <div className="mt-1 flex items-center gap-2">
        {onBack ? (
          <button
            type="button"
            onClick={onBack}
            className="rounded-9 px-3 py-[9px] text-13 font-medium text-ink-2 hover:bg-hover hover:text-ink"
          >
            {backLabel}
          </button>
        ) : null}
        <button
          type="button"
          onClick={onNext}
          disabled={disabled || busy}
          aria-busy={busy || undefined}
          title={disabled && hint ? hint : undefined}
          className="ml-auto rounded-9 bg-brand px-[18px] py-[10px] text-13 font-semibold text-white transition-colors duration-[120ms] hover:bg-brand-dark disabled:cursor-not-allowed disabled:opacity-55"
        >
          {nextLabel}
        </button>
      </div>
    </>
  )
}

/** A field's label over its box: 12 px 500 ink-2, 6 px above (007). */
export const SETUP_LABEL = 'text-12 font-medium text-ink-2'

/** 007's field: white, 12 px round, raised, 13.5 px; the focus is the 1.5 px brand outline the
 *  dialogs' fields take (the border is always there, transparent until then). */
export const SETUP_FIELD =
  'box-border w-full rounded-12 border-[1.5px] border-transparent bg-card-white px-[10.5px] ' +
  'h-[37px] py-0 text-135 leading-[normal] text-ink shadow-field outline-none ' +
  'focus:border-brand focus:shadow-none focus-visible:outline-none'
