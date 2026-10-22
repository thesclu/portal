import createClient, { type Middleware } from 'openapi-fetch'

import { inSetup, isSetupRefusal } from '../lib/setup'
import type { components, paths } from './schema'

export type Schemas = components['schemas']

function cookie(name: string): string | null {
  const found = document.cookie.split('; ').find((part) => part.startsWith(`${name}=`))
  return found ? decodeURIComponent(found.slice(name.length + 1)) : null
}

/** Paths that answer 401 as part of their ordinary job; a 401 there is not "you were signed out". */
const AUTH_PATHS = ['/api/core/auth/me', '/api/core/auth/csrf']

let leaving = false

/** P25a: the server's answer when the last passkey check is older than Settings allows. */
function isReverify(body: unknown): boolean {
  return (
    typeof body === 'object' &&
    body !== null &&
    (body as { code?: unknown }).code === 'reverify_required'
  )
}

/** Called when the person signs out on purpose, so the 401s that follow are not reported as
 *  "you were signed out". */
export function expectSignOut(): void {
  leaving = true
}

const session: Middleware = {
  onRequest({ request }) {
    // Django's session auth wants the CSRF token echoed on every write. The cookie is readable
    // (CSRF_COOKIE_HTTPONLY is off for exactly this) and is rotated at sign-in, so it is read
    // fresh each time rather than remembered.
    if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method)) {
      const token = cookie('csrftoken')
      if (token) request.headers.set('X-CSRFToken', token)
    }
    return request
  },
  async onResponse({ request, response }) {
    const path = new URL(request.url).pathname
    if (
      response.status === 401 &&
      !leaving &&
      !AUTH_PATHS.includes(path) &&
      !window.location.pathname.startsWith('/sign-in')
    ) {
      // The session ended while the page was open: say so on the sign-in screen and come back
      // to where the person was. P25a: or the passkey check fell due (`reverify_required`):
      // through Keycloak again, which asks only for the passkey, and back.
      const back = window.location.pathname + window.location.search
      const body: unknown = await response
        .clone()
        .json()
        .catch(() => null)
      const reverify = isReverify(body)
      window.location.assign(
        `/sign-in?${reverify ? 'reverify=1' : 'expired=1'}&next=${encodeURIComponent(back)}`,
      )
    }
    if (response.status === 403 && !inSetup(window.location.pathname)) {
      // P23a: the server holds a person at the new-user setup (an agreement's new version was
      // published while the page was open, say). Go there; it comes back to Home when done.
      const body: unknown = await response
        .clone()
        .json()
        .catch(() => null)
      if (isSetupRefusal(response.status, body)) window.location.assign('/setup')
    }
    return response
  },
}

export const api = createClient<paths>({ baseUrl: '/', credentials: 'include' })
api.use(session)

/** Sets the CSRF cookie. Called once before the app renders, and again after signing out. */
export async function primeCsrf(): Promise<void> {
  await api.GET('/api/core/auth/csrf')
}
