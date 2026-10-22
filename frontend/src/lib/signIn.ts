/**
 * P25a: the addresses of the sign-in, which is Keycloak's. The browser goes to the portal's own
 * server (`/api/core/auth/oidc/login`), which runs the authorization code flow and sends it on
 * to Keycloak's themed pages; the SPA never sees a token.
 */

const LOGIN = '/api/core/auth/oidc/login'
const LAST_EMAIL = 'sclu.signin.email'

/** Only a path on this site: one slash, no scheme, no backslash. Anything else is Home. */
export function safePath(next: string | null | undefined): string {
  return next && next.startsWith('/') && !next.startsWith('//') && !next.includes('\\') ? next : '/'
}

/** The address that starts a sign-in and comes back to `next`. `expired` asks Keycloak's page to
 *  say the session ended on its own (005), with `loginHint` as the address it asks the password
 *  for; `action` is one of Keycloak's application-initiated actions (a credential page). */
export function signInAddress({
  next,
  expired = false,
  loginHint = '',
  action = '',
}: {
  next?: string | null
  expired?: boolean
  loginHint?: string
  action?: string
}): string {
  const query = new URLSearchParams({ next: safePath(next) })
  if (expired) query.set('expired', '1')
  if (loginHint) query.set('login_hint', loginHint)
  if (action) query.set('kc_action', action)
  return `${LOGIN}?${query.toString()}`
}

/** The address last signed in with on this browser, for 005's "signed out after N hours". */
export function lastEmail(): string {
  try {
    return localStorage.getItem(LAST_EMAIL) ?? ''
  } catch {
    return ''
  }
}

export function rememberEmail(email: string): void {
  try {
    localStorage.setItem(LAST_EMAIL, email)
  } catch {
    /* a private window */
  }
}

/** What /signed-out says for each reason the server gives (`?state=`), in the card's words. */
export const SIGNED_OUT_REASONS: Record<string, string> = {
  not_invited: 'This account isn’t on the portal yet. Ask an admin to invite you.',
  deactivated: 'This account can no longer sign in. Ask an admin if that’s a mistake.',
  no_sign_in: 'This account doesn’t sign in to the portal.',
  refused: 'That sign-in didn’t go through. Try again.',
  unavailable: 'Signing in isn’t available just now. Try again in a minute.',
  cancelled: 'Sign-in was cancelled.',
}

export function signedOutReason(state: string | null): string | null {
  return state ? (SIGNED_OUT_REASONS[state] ?? SIGNED_OUT_REASONS.refused) : null
}

/** RP-initiated logout: post the fields to Keycloak's end-session endpoint (a form, so the ID
 *  token stays out of the address). */
export function postEndSession(endSession: { url: string; fields: Record<string, string> }): void {
  const form = document.createElement('form')
  form.method = 'POST'
  form.action = endSession.url
  for (const [name, value] of Object.entries(endSession.fields)) {
    const input = document.createElement('input')
    input.type = 'hidden'
    input.name = name
    input.value = value
    form.appendChild(input)
  }
  document.body.appendChild(form)
  form.submit()
}
