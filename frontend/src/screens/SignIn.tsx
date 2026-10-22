import { useEffect } from 'react'
import { Navigate, useSearchParams } from 'react-router'

import type { Schemas } from '../api/client'
import { landing } from '../app/nav'
import { useSessionQuery } from '../app/session'
import { Loading } from '../components/States'
import { lastEmail, signInAddress } from '../lib/signIn'

/** Only ever send someone back to a path inside this site. */
function safeNext(next: string | null, me: Schemas['MeOut']): string {
  return next && next.startsWith('/') && !next.startsWith('//') ? next : landing(me)
}

/**
 * Sign in (P25a): Keycloak's pages, themed to the design (001–005), take the password and the
 * passkey; this route only sends the browser there (a full page load to the portal's server,
 * which runs the code flow) and comes back to `next`. After a session ended on its own
 * (`?expired=1`) Keycloak's page says so and asks only for the password of the address last
 * signed in with on this browser (005). A passkey check that fell due (`?reverify=1`) goes the
 * same way and Keycloak asks only for the passkey.
 */
export function SignIn() {
  const [params] = useSearchParams()
  const session = useSessionQuery()
  const signedIn = Boolean(session.data)
  const waiting = session.isPending

  useEffect(() => {
    if (signedIn || waiting) return
    const expired = Boolean(params.get('expired'))
    window.location.replace(
      signInAddress({
        next: params.get('next'),
        expired,
        loginHint: expired ? lastEmail() : '',
      }),
    )
  }, [signedIn, waiting, params])

  if (session.data) return <Navigate to={safeNext(params.get('next'), session.data)} replace />
  return (
    <div className="flex h-full items-center justify-center bg-page">
      <Loading what="Signing you in" />
    </div>
  )
}
