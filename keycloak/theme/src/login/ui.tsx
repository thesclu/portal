/**
 * The pieces every sign-in page is made of, drawn as design v6 draws them (001–005) and as the
 * portal's own signed-out screens drew them (AuthCard, Field, Input): the centred card on the page
 * grey, the raised 40 px fields, the 40 px brand pill, the one error line and the quiet links.
 * Sizes and colours are the portal's tokens (src/portal/tokens.css), never literals.
 */
import { useInitialize } from 'keycloakify/login/Template.useInitialize'
import { kcSanitize } from 'keycloakify/lib/kcSanitize'
import {
  useEffect,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
} from 'react'

import logo from '../portal/sclu-logo.png'
import type { KcContext } from './KcContext'

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ')
}

/**
 * The card (design 001–005): 360 px of content inside 32/32/28 px of padding, 16 px round, the
 * float shadow, a 16 px gap; the round 48 px logo over the portal's name in 20 px Libre Franklin
 * 800. Everything inside reads at the browser's normal line height, as the design's does.
 */
export function Card({
  kcContext,
  title,
  children,
}: {
  kcContext: KcContext
  /** The browser tab's title, before " · SCLU Portal". */
  title: string
  children: ReactNode
}) {
  // Keycloak's own page scripts: the check that reloads this page when the sign-in finished in
  // another tab. The theme loads no stylesheet of Keycloak's.
  const { isReadyToRender } = useInitialize({ kcContext, doUseDefaultCss: false })
  const name = kcContext.realm.displayName || 'SCLU Portal'
  useEffect(() => {
    document.title = `${title} · ${name}`
  }, [title, name])
  if (!isReadyToRender) return null
  return (
    <div className="flex min-h-full items-center justify-center bg-page p-6">
      <main className="flex w-[424px] max-w-full flex-col gap-4 rounded-16 bg-card px-8 pt-8 pb-7 leading-[normal] shadow-float">
        <div className="mb-1.5 flex flex-col items-center gap-3">
          <img src={logo} alt="" className="size-12 shrink-0 rounded-pill" />
          <h1 className="font-display text-20 font-extrabold tracking-dialog text-ink">{name}</h1>
        </div>
        {children}
      </main>
    </div>
  )
}

/** A line of explanation under the logo: 13 px at 1.6, centred, ink-2 (ink for 005's notice). */
export function Text({
  children,
  ink = false,
  role,
}: {
  children: ReactNode
  ink?: boolean
  role?: 'status' | 'alert'
}) {
  return (
    <p
      role={role}
      className={cx('text-center text-13 leading-[1.6]', ink ? 'text-ink' : 'text-ink-2')}
    >
      {children}
    </p>
  )
}

/** A message Keycloak wrote (it may carry Keycloak's own sanitised markup, such as a line break). */
export function Message({ summary }: { summary: string }) {
  return <span dangerouslySetInnerHTML={{ __html: kcSanitize(summary) }} />
}

/** The label over a field, 12.5 px ink-2, 6 px above it; the 12 px muted hint under it. */
export function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: ReactNode
  children: ReactNode
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-125 text-ink-2">{label}</span>
      {children}
      {hint ? <span className="text-12 text-muted">{hint}</span> : null}
    </label>
  )
}

/**
 * The raised field (001): white, 12 px round, the field shadow; with the focus the shadow turns
 * brand (--shadow-primary). In error (001 "Wrong password") it is outlined instead: 1.5 px of the
 * error line, 10 px round, no shadow. The 1.5 px border is always there, transparent until then,
 * so the text never moves. Passwords are spaced as the design draws the dots.
 */
export function Input({
  invalid,
  className,
  ...rest
}: InputHTMLAttributes<HTMLInputElement> & { invalid?: boolean }) {
  return (
    <input
      {...rest}
      aria-invalid={invalid || undefined}
      className={cx(
        'box-border h-10 w-full rounded-12 border-[1.5px] border-transparent bg-card-white px-[10.5px] text-13 text-ink',
        'shadow-field outline-none placeholder:text-muted focus:shadow-primary focus-visible:outline-none',
        'aria-invalid:rounded-10 aria-invalid:border-red-line aria-invalid:px-3 aria-invalid:shadow-none',
        rest.type === 'password' && 'tracking-[.2em]',
        className,
      )}
    />
  )
}

/** A value shown in the raised field's shape but not edited (002 "Email" on the invite). */
export function ReadonlyBox({ children }: { children: ReactNode }) {
  return (
    <div className="box-border flex h-10 items-center truncate rounded-12 bg-card-white px-3 text-13 text-ink shadow-field">
      {children}
    </div>
  )
}

/** The card's one button: the 40 px brand pill, 13.5 px 500. */
export function Pill({
  busy,
  children,
  className,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { busy?: boolean }) {
  return (
    <button
      type="submit"
      {...rest}
      disabled={rest.disabled || busy}
      aria-busy={busy || undefined}
      className={cx(
        'mt-1 inline-flex h-10 w-full shrink-0 items-center justify-center rounded-pill bg-brand text-135 leading-none font-medium text-white',
        'transition-colors duration-[120ms] hover:bg-brand-dark disabled:cursor-not-allowed disabled:opacity-55',
        className,
      )}
    >
      {children}
    </button>
  )
}

/** The pill as a link, where the next step is an address rather than a form. */
export function PillLink({
  href,
  children,
  ...rest
}: { href: string; children: ReactNode } & Record<`data-${string}`, string>) {
  return (
    <a
      {...rest}
      href={href}
      className="mt-1 inline-flex h-10 w-full shrink-0 items-center justify-center rounded-pill bg-brand text-135 leading-none font-medium text-white transition-colors duration-[120ms] hover:bg-brand-dark hover:text-white"
    >
      {children}
    </a>
  )
}

/** The single error line: 12.5 px in danger, pulled up under the field it is about. */
export function ErrorLine({ children, id }: { children: ReactNode; id?: string }) {
  return (
    <p role="alert" id={id} className="-mt-2 text-125 text-danger">
      {children}
    </p>
  )
}

const QUIET = 'block w-full text-center text-125 font-medium text-brand hover:text-brand-dark'

/** A quiet link under a card's form: "Forgot password", "Back to sign in". */
export function QuietLink({
  href,
  children,
  ...rest
}: { href: string; children: ReactNode } & Record<`data-${string}`, string>) {
  return (
    <a {...rest} href={href} className={QUIET}>
      {children}
    </a>
  )
}

/** The same, as a button: it submits its form (Cancel) or runs `onClick`. */
export function QuietButton({ className, ...rest }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button type="button" {...rest} className={cx(QUIET, className)} />
}

/**
 * A quiet link that posts to Keycloak: "Use a recovery code", "Use your passkey", "Try another
 * way". Keycloak takes `authenticationExecution` (go straight to that way of signing in) or
 * `tryAnotherWay` (the list of ways) on the page's own action.
 */
export function PostLink({
  action,
  fields,
  children,
}: {
  action: string
  fields: Record<string, string>
  children: ReactNode
}) {
  return (
    <form action={action} method="post" className="contents">
      {Object.entries(fields).map(([name, value]) => (
        <input key={name} type="hidden" name={name} value={value} />
      ))}
      <button type="submit" className={QUIET}>
        {children}
      </button>
    </form>
  )
}

/** A 16 px box and its 12.5 px label (Keycloak's "Sign out other devices", the codes' check). */
export function Checkbox({
  label,
  ...rest
}: InputHTMLAttributes<HTMLInputElement> & { label: ReactNode }) {
  return (
    <label className="flex items-start gap-2 text-125 leading-[1.5] text-ink-2">
      <input type="checkbox" {...rest} className="mt-px size-4 shrink-0 accent-brand" />
      <span>{label}</span>
    </label>
  )
}

/**
 * The six code boxes (004 right): 40 × 48, 10 px round, 18 px digits. The box waiting for the next
 * digit has the brand outline, the empty ones the light line, a filled one none. One real input
 * lies over them, invisible, so typing, paste and the browser's one-time-code fill all work.
 */
export function CodeBoxes({
  name,
  invalid,
  length = 6,
  describedBy,
}: {
  name: string
  invalid: boolean
  length?: number
  describedBy?: string
}) {
  const [value, setValue] = useState('')
  const digits = value.replace(/\D/g, '').slice(0, length)
  const next = Math.min(digits.length, length - 1)
  return (
    <div className="relative flex justify-center gap-2">
      {Array.from({ length }, (_, i) => (
        <span
          key={i}
          aria-hidden
          className={cx(
            'box-border inline-flex h-12 w-10 items-center justify-center rounded-10 border-[1.5px] text-18 text-ink',
            invalid
              ? 'border-red-line'
              : i === next && digits.length < length
                ? 'border-brand'
                : i < digits.length
                  ? 'border-transparent'
                  : 'border-line-2',
          )}
        >
          {digits[i] ?? ''}
        </span>
      ))}
      <input
        name={name}
        aria-label="Code"
        aria-describedby={describedBy}
        inputMode="numeric"
        autoComplete="one-time-code"
        pattern="[0-9]*"
        maxLength={length}
        required
        autoFocus
        value={digits}
        aria-invalid={invalid || undefined}
        onChange={(event) => setValue(event.target.value)}
        className="absolute inset-0 h-full w-full cursor-text opacity-0"
      />
    </div>
  )
}
