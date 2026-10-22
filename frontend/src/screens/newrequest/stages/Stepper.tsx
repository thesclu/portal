/**
 * The stages of a new request as 044–052 draw them: a 21 px circle and a 12.5 px name per stage,
 * 40 px rules between them; a tick on a green tint for a stage passed, the brand circle for the
 * one here, an outlined number ahead. The design's fourth stage, "Edit by agency (optional)", is
 * the drafting engine's (P21d) and is not drawn until it exists (design v2 rule 8), so Review &
 * file is the fourth here. The action the design puts at the right of the row ("Write from
 * scratch") goes in `right`. The circles are content-box as drawn: a border adds to them.
 */
import { Fragment, type ReactNode } from 'react'

import { cx } from '../../../components/ui'
import { STEPS, type StepKey } from './stages'

export function Stepper({
  current,
  onGo,
  right,
}: {
  current: StepKey
  /** When given, a stage already passed is a button that goes back to it. */
  onGo?: (key: StepKey) => void
  right?: ReactNode
}) {
  const at = STEPS.findIndex((step) => step.key === current)
  return (
    <div className="flex w-full items-center leading-[normal]">
      <ol aria-label="Stage" className="contents">
        {STEPS.map((step, index) => {
          const done = index < at
          const here = index === at
          const body = (
            <>
              <span
                aria-hidden
                className={cx(
                  'box-content inline-flex size-[21px] shrink-0 items-center justify-center rounded-pill text-105 font-semibold',
                  here
                    ? 'bg-brand text-white'
                    : done
                      ? 'border border-green-line bg-ok-tint text-ok'
                      : 'border-[1.5px] border-line-control text-muted',
                )}
              >
                {done ? '✓' : index + 1}
              </span>
              <span className={cx('text-125', here ? 'font-semibold' : 'font-medium')}>
                {done ? <span className="sr-only">Done: </span> : null}
                {step.label}
              </span>
            </>
          )
          const tone = here ? 'text-ink' : done ? 'text-ok' : 'text-muted'
          return (
            <Fragment key={step.key}>
              {index > 0 ? <li aria-hidden className="mx-3 h-[1.5px] w-10 bg-line" /> : null}
              <li aria-current={here ? 'step' : undefined} className="flex">
                {onGo && done ? (
                  <button
                    type="button"
                    className={cx('flex items-center gap-2 hover:underline', tone)}
                    onClick={() => onGo(step.key)}
                  >
                    {body}
                  </button>
                ) : (
                  <span className={cx('flex items-center gap-2', tone)}>{body}</span>
                )}
              </li>
            </Fragment>
          )
        })}
      </ol>
      {right ? <div className="ml-auto flex shrink-0 items-center">{right}</div> : null}
    </div>
  )
}
