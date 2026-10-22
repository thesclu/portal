import type { ReactNode } from 'react'
import { Link } from 'react-router'

import { Button, cx } from '../../components/ui'

/**
 * The centred card every signed-out screen is drawn in (design 001–005): 360 px on the page
 * grey (360 px of content inside 32 px of padding), 16 px radius, the float shadow, a 16 px gap; the round
 * 48 px logo over "SCLU Portal" in 20 px Libre Franklin 800.
 */
export function AuthCard({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-full items-center justify-center bg-page p-6">
      <div className="flex w-[424px] flex-col gap-4 rounded-16 bg-card px-8 pt-8 pb-7 shadow-float">
        <div className="mb-1.5 flex flex-col items-center gap-3">
          <img src="/brand/sclu-logo.png" alt="" className="size-12 rounded-pill" />
          <h1 className="font-display text-20 leading-[normal] font-extrabold tracking-dialog text-ink">
            SCLU Portal
          </h1>
        </div>
        {children}
      </div>
    </div>
  )
}

/** A line of explanation under the logo: 13 px, 1.6, ink-2, centred. */
export function AuthText({ children, ink = false }: { children: ReactNode; ink?: boolean }) {
  return (
    <p className={cx('text-center text-13 leading-[1.6]', ink ? 'text-ink' : 'text-ink-2')}>
      {children}
    </p>
  )
}

/** The card's one button: the 40 px brand pill, 13.5 px 500. */
export function AuthButton({
  busy,
  children,
  ...rest
}: {
  busy?: boolean
  disabled?: boolean
  type?: 'submit' | 'button'
  onClick?: () => void
  children: ReactNode
}) {
  return (
    <Button kind="pill" busy={busy} {...rest} className="mt-1 h-10 w-full text-135 font-medium">
      {children}
    </Button>
  )
}

/** The single non-specific error line: 12.5 px in danger, pulled up under the field. */
export function AuthError({ children }: { children: ReactNode }) {
  return (
    <p role="alert" className="-mt-2 text-125 text-danger">
      {children}
    </p>
  )
}

/** A quiet link under a card's form: "Forgot password", "Back to sign in". */
export function AuthLink({
  to,
  onClick,
  children,
}: {
  to?: string
  onClick?: () => void
  children: ReactNode
}) {
  const className = 'block text-center text-125 font-medium text-brand hover:text-brand-dark'
  return to ? (
    <Link to={to} className={className}>
      {children}
    </Link>
  ) : (
    <button type="button" onClick={onClick} className={cx(className, 'w-full')}>
      {children}
    </button>
  )
}

/** The session query's key. After a sign-in, an accepted invite or a reset, it is primed with the
 *  `MeOut` the API answered and everything else is fetched afresh for that person. */
export const ME_KEY = ['get', '/api/core/auth/me', {}] as const
