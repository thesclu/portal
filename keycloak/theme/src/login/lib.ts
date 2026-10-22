/**
 * What the pages read beyond Keycloak's own context: the portal's hints in the address, notes this
 * tab keeps between two of Keycloak's pages, and the action token in an emailed link.
 */

/** This tab's notes (sessionStorage), which a private window or blocked storage may refuse. */
export const note = {
  get<T>(key: string): T | null {
    try {
      const raw = sessionStorage.getItem(`sclu.${key}`)
      return raw === null ? null : (JSON.parse(raw) as T)
    } catch {
      return null
    }
  },
  set(key: string, value: unknown) {
    try {
      sessionStorage.setItem(`sclu.${key}`, JSON.stringify(value))
    } catch {
      /* the page still works without it */
    }
  },
  drop(key: string) {
    try {
      sessionStorage.removeItem(`sclu.${key}`)
    } catch {
      /* nothing to drop */
    }
  },
}

const path = () => window.location.pathname
const query = () => new URLSearchParams(window.location.search)

/**
 * 005 "Signed out after inactivity": the portal sends a person whose session ended on its own to
 * the sign-in with `sclu_expired=<hours>` (Keycloak ignores the parameter) and `login_hint`. The
 * hours are kept for this tab, so the notice stays after a mistyped password (Keycloak's next page
 * is /login-actions/authenticate, without the parameter); a fresh sign-in without it clears them.
 */
export function expiredHours(): number | null {
  const given = query().get('sclu_expired')
  if (given !== null) {
    const hours = Number.parseInt(given, 10)
    if (Number.isFinite(hours) && hours > 0 && hours < 1000) {
      note.set('expired', hours)
      return hours
    }
    note.drop('expired')
    return null
  }
  if (path().endsWith('/protocol/openid-connect/auth')) {
    note.drop('expired')
    return null
  }
  return note.get<number>('expired')
}

/**
 * P25c: someone setting up their first second step chose an authenticator app on the passkey
 * page, so the portal starts a new sign-in with `sclu_second_step=app` (Keycloak ignores the
 * parameter; the sign-in asks for the scope that sends them to the app's setup). The sign-in page
 * says why it asks for the password again; kept for this tab like the expired hours, so a mistyped
 * password keeps the line, and cleared by a fresh sign-in without it.
 */
export function choseApp(): boolean {
  const given = query().get('sclu_second_step')
  if (given !== null) {
    note.set('second-step', given === 'app')
    return given === 'app'
  }
  if (path().endsWith('/protocol/openid-connect/auth')) {
    note.drop('second-step')
    return false
  }
  return note.get<boolean>('second-step') === true
}

/**
 * P25c: whether an administrator allows authenticator apps (Settings → System → Sign-in), asked of
 * the portal, which serves these pages on its own address (`GET /api/core/auth/second-steps`).
 * False until it answers, and if it cannot: the passkey alone, as with the switch off.
 */
export async function appAllowed(): Promise<boolean> {
  try {
    const answer = await fetch('/api/core/auth/second-steps', {
      credentials: 'omit',
      headers: { Accept: 'application/json' },
    })
    if (!answer.ok) return false
    const body = (await answer.json()) as { authenticator_app?: unknown }
    return body.authenticator_app === true
  } catch {
    return false
  }
}

/** The payload of a JSON web token, unverified: only ever used to choose words. */
function payloadOf(token: string): Record<string, unknown> | null {
  try {
    const part = token.split('.')[1]
    if (!part) return null
    const json = atob(part.replace(/-/g, '+').replace(/_/g, '/'))
    const value: unknown = JSON.parse(
      new TextDecoder().decode(Uint8Array.from(json, (c) => c.charCodeAt(0))),
    )
    return value && typeof value === 'object' ? (value as Record<string, unknown>) : null
  } catch {
    return null
  }
}

export type ActionToken = {
  /** `execute-actions` (an invite, or an admin's "set a new password") or `reset-credentials`. */
  type: string
  email: string | null
}

/** The emailed link's action token, on a page shown at /login-actions/action-token. */
export function actionToken(): ActionToken | null {
  if (!path().endsWith('/login-actions/action-token')) return null
  const key = query().get('key')
  const payload = key ? payloadOf(key) : null
  if (!payload) return null
  return {
    type: typeof payload.typ === 'string' ? payload.typ : '',
    email: typeof payload.eml === 'string' ? payload.eml : null,
  }
}

export type Invite = { invitedBy: string; role: string; email: string | null; at?: number }

const short = (value: string | null) => (value ?? '').trim().slice(0, 120)
// An invite read from a link is about that visit: an hour later this tab is doing something else.
const INVITE_KEPT_MS = 60 * 60 * 1000

function keptInvite(): Invite | null {
  const kept = note.get<Invite>('invite')
  return kept && Date.now() - (kept.at ?? 0) < INVITE_KEPT_MS ? kept : null
}

/**
 * The invite (002 left). Keycloak shows a login page nothing about the person but their address,
 * so the invite email (src/email, which can read the person's attributes) adds who invited them
 * and as what to its link's fragment, `#invited_by=…&role=…` (a fragment never reaches a server
 * or its logs). The page at that link keeps it for this tab, and the password page after it reads
 * it back for the same address.
 */
export function rememberInvite(email: string | null): Invite | null {
  const hash = new URLSearchParams(window.location.hash.replace(/^#/, ''))
  const invitedBy = short(hash.get('invited_by'))
  if (invitedBy !== '') {
    const invite = { invitedBy, role: short(hash.get('role')), email, at: Date.now() }
    note.set('invite', invite)
    return invite
  }
  // The same link opened again in this tab, after the fragment was already read.
  return inviteFor(email ?? undefined)
}

export function inviteFor(email: string | undefined): Invite | null {
  const kept = keptInvite()
  return kept && email && kept.email?.toLowerCase() === email.toLowerCase() ? kept : null
}

/** The address a reset link was asked for (003 left), kept by the forgot form for this tab. */
export const resetEmail = {
  set: (email: string) => note.set('reset', email.trim()),
  get: () => note.get<string>('reset'),
}

/**
 * A new passkey's name in the person's list, made from this browser and system ("Chrome on
 * macOS"), in place of Keycloak's prompt asking the person to type one.
 */
export function passkeyLabel(): string {
  const ua = navigator.userAgent
  const browser = /Edg\//.test(ua)
    ? 'Edge'
    : /Firefox\//.test(ua)
      ? 'Firefox'
      : /Chrome\//.test(ua)
        ? 'Chrome'
        : /Safari\//.test(ua)
          ? 'Safari'
          : null
  const system = /iPhone|iPad|iPod/.test(ua)
    ? 'iOS'
    : /Android/.test(ua)
      ? 'Android'
      : /Mac OS X|Macintosh/.test(ua)
        ? 'macOS'
        : /Windows/.test(ua)
          ? 'Windows'
          : /CrOS/.test(ua)
            ? 'ChromeOS'
            : /Linux/.test(ua)
              ? 'Linux'
              : null
  if (browser && system) return `${browser} on ${system}`
  return browser ?? system ?? 'Passkey'
}
