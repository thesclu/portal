import { useLayoutEffect, useRef, useState } from 'react'

import type { Schemas } from '../../api/client'
import type { Me } from '../../app/session'
import { CheckIcon } from '../../components/icons'
import { cx } from '../../components/ui'
import { civilDayOf, formatDate } from '../../lib/dates'
import { readToEnd, scrollThumb } from '../../lib/setup'
import { SetupActions } from './parts'

type Agreement = Schemas['SetupAgreementOut']

/**
 * Steps 4 and 5, an agreement (010 confidentiality, 011 working as an intern): its clauses on the
 * soft grey, each lead in 600 ink. The confidentiality agreement scrolls in a 320 px box with the
 * thumb 010 draws (the browser's own is hidden) and a fade at its foot until the end is reached;
 * it is signed on a line with the signature from step 3, the name, the date and the version.
 * The button waits until the text has been read to its end (a box that does not scroll is read)
 * and the box is ticked. An agreement already agreed to in this run is shown ticked, and the
 * button only moves on.
 */
export function AgreementStep({
  me,
  agreement,
  signatureUrl,
  scrolls,
  signed,
  checkLabel,
  agreeLabel,
  moveLabel,
  onBack,
  onAgree,
}: {
  me: Me
  agreement: Agreement
  signatureUrl: string | null
  /** 010: the text scrolls in a fixed box; 011 is drawn at its own height. */
  scrolls: boolean
  /** 010 draws the signature line; 011 does not. */
  signed: boolean
  checkLabel: string
  /** The button before agreeing (010 "Agree", 011 "Finish")… */
  agreeLabel: string
  /** …and once agreed in this run ("Next", or "Finish" on the last step). */
  moveLabel: string
  onBack?: () => void
  /** Records the acceptance (when not yet agreed) and moves on. */
  onAgree: (alreadyAgreed: boolean) => Promise<void>
}) {
  const agreed = agreement.accepted_at != null
  const box = useRef<HTMLDivElement>(null)
  const [checked, setChecked] = useState(agreed)
  const [read, setRead] = useState(agreed || !scrolls)
  const [atEnd, setAtEnd] = useState(!scrolls)
  const [thumb, setThumb] = useState<{ top: number; height: number } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // The date the line is signed on, read once: today until it has been agreed to.
  const [today] = useState(() => new Date().toISOString())

  function measure() {
    const element = box.current
    if (!element) return
    const { scrollTop, clientHeight, scrollHeight } = element
    setThumb(scrollThumb(scrollTop, clientHeight, scrollHeight))
    const end = readToEnd(scrollTop, clientHeight, scrollHeight)
    setAtEnd(end)
    if (end) setRead(true)
  }

  useLayoutEffect(() => {
    if (scrolls) measure()
  }, [scrolls, agreement.id])

  async function agree() {
    setBusy(true)
    setError(null)
    try {
      await onAgree(agreed)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'It was not recorded. Try again.')
      setBusy(false)
    }
  }

  const clauses = agreement.clauses.map((clause, at) => (
    <p key={at} className="text-13 leading-[1.6] text-ink-2">
      {clause.lead ? <span className="font-semibold text-ink">{clause.lead}</span> : null}
      {clause.lead && clause.text ? ' ' : null}
      {clause.text}
    </p>
  ))
  const when = agreement.accepted_at ?? today

  return (
    <>
      <h1 className="font-display text-24 font-extrabold tracking-title text-ink">
        {agreement.title}
      </h1>
      {scrolls ? (
        <div className="relative h-80 overflow-hidden rounded-14 bg-surface-soft">
          <div
            ref={box}
            onScroll={measure}
            tabIndex={0}
            role="document"
            aria-label={`${agreement.title}, version ${agreement.version}`}
            data-agreement-text
            className="box-border flex h-full flex-col gap-3 overflow-y-auto py-4 pr-[22px] pl-[18px] outline-none [scrollbar-width:none] focus-visible:shadow-ring-focus-strong [&::-webkit-scrollbar]:hidden"
          >
            {clauses}
          </div>
          {thumb ? (
            <span
              aria-hidden
              className="absolute right-[7px] w-1 rounded-pill bg-line-2"
              style={{ top: thumb.top, height: thumb.height }}
            />
          ) : null}
          {atEnd ? null : (
            <div
              aria-hidden
              className="pointer-events-none absolute inset-x-0 bottom-0 h-14 bg-linear-to-b from-surface-soft/0 to-surface-soft"
            />
          )}
        </div>
      ) : (
        <div
          role="document"
          aria-label={`${agreement.title}, version ${agreement.version}`}
          className="flex flex-col gap-3 rounded-14 bg-surface-soft px-[18px] py-4"
        >
          {clauses}
        </div>
      )}
      {signed ? (
        <div className="flex flex-col gap-1.5">
          <div className="box-content flex h-[46px] items-end border-b border-line-2 px-0.5 pb-0.5">
            {signatureUrl ? (
              <img
                src={signatureUrl}
                alt="Your signature"
                className="max-h-[42px] max-w-[60%] object-contain"
              />
            ) : (
              <span className="font-signature text-signature-sm leading-none text-signature-ink">
                {me.name}
              </span>
            )}
          </div>
          <span className="text-12 text-muted">
            {me.name} · {formatDate(civilDayOf(when, me.timezone))} · Agreement v{agreement.version}
          </span>
        </div>
      ) : null}
      <label className={cx('flex items-center gap-2.5', agreed ? '' : 'cursor-pointer')}>
        <input
          type="checkbox"
          className="peer sr-only"
          checked={checked}
          disabled={agreed}
          onChange={(event) => setChecked(event.target.checked)}
        />
        <span
          aria-hidden
          className={cx(
            'box-border inline-flex size-4 shrink-0 items-center justify-center rounded-4 peer-focus-visible:shadow-ring-focus-strong',
            checked ? 'bg-brand' : 'border-[1.5px] border-line-control bg-card-white',
          )}
        >
          {checked ? <CheckIcon size={10} width={3.4} stroke="var(--color-card-white)" /> : null}
        </span>
        <span className="text-135 text-ink">{checkLabel}</span>
      </label>
      <SetupActions
        onBack={onBack}
        nextLabel={agreed ? moveLabel : agreeLabel}
        onNext={() => void agree()}
        disabled={!agreed && !(read && checked)}
        busy={busy}
        error={error}
        hint={read ? 'Tick the box first' : 'Read to the end first'}
      />
    </>
  )
}
