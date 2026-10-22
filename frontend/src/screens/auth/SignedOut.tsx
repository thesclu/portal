import { Navigate, useSearchParams } from 'react-router'

import { signedOutReason } from '../../lib/signIn'
import { AuthCard, AuthLink, AuthText } from './AuthCard'

/**
 * Where Keycloak sends the browser after signing out (its post-logout address, P25a). An
 * ordinary sign-out goes straight on to the sign-in (001, Keycloak's page). A sign-in the portal
 * refused (not invited, deactivated, a requester, a check that failed) arrives with the reason as
 * `state` and is told why, in 005's "invite link expired" card, with the way back to sign in.
 */
export function SignedOut() {
  const [params] = useSearchParams()
  const reason = signedOutReason(params.get('state'))
  if (!reason) return <Navigate to="/sign-in" replace />
  return (
    <AuthCard>
      <AuthText>
        <span role="alert">{reason}</span>
      </AuthText>
      <AuthLink to="/sign-in">Back to sign in</AuthLink>
    </AuthCard>
  )
}
