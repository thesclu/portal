import { useQueryClient } from '@tanstack/react-query'
import { createContext, useContext, type ReactNode } from 'react'

import { expectSignOut, primeCsrf, type Schemas } from '../api/client'
import { $api } from '../api/query'
import { isFullStaff as isFullStaffRole, needsApproval as needsApprovalRole } from '../lib/roles'
import { postEndSession } from '../lib/signIn'

export type Me = Schemas['MeOut']

const SessionContext = createContext<Me | null>(null)

/** The signed-in person, or null outside a signed-in route (the error pages are drawn in both
 *  places). */
export function useMaybeMe(): Me | null {
  return useContext(SessionContext)
}

/** The signed-in person. Only rendered inside `RequireSession`, so it is never null there. */
export function useMe(): Me {
  const me = useContext(SessionContext)
  if (!me) throw new Error('useMe() outside a signed-in route')
  return me
}

export function useSessionQuery() {
  return $api.useQuery('get', '/api/core/auth/me', {}, { retry: false, staleTime: 60_000 })
}

export function SessionProvider({ me, children }: { me: Me; children: ReactNode }) {
  return <SessionContext.Provider value={me}>{children}</SessionContext.Provider>
}

/** A portal administrator: may open Users and change settings. The server decides; this only
 *  chooses what to draw. */
export function isAdmin(me: Me): boolean {
  return me.is_staff
}

/** A requester: a signed-in member of the public whose requests the SCLU files. They see their
 *  own requests and may suggest one; nothing else (P13). */
export function isRequester(me: Me): boolean {
  return me.records_role === 'requester'
}

/** Admin, staff or staff attorney: the organisation's own tables and unplaced mail (P20h). */
export function isFullStaff(me: Me): boolean {
  return isFullStaffRole(me.records_role)
}

/** An intern: what they write leaves the portal only after an approver's yes (P20h, 42b). */
export function needsApproval(me: Me): boolean {
  return needsApprovalRole(me.records_role)
}

/** The records administrator: gives and takes access, moves a deadline's date (P20h; the
 *  retired Director mark's doors, 42d). */
export function isRecordsAdmin(me: Me): boolean {
  return me.records_role === 'admin'
}

/** Sign out (P25a): the portal session ends on the server, then the browser posts Keycloak's
 *  end-session form, which ends the Keycloak session too and comes back to /signed-out (and on to
 *  the sign-in). Without the form (Keycloak not reached), straight to /signed-out. */
export function useSignOut() {
  const client = useQueryClient()
  const signOut = $api.useMutation('post', '/api/core/auth/logout')
  return async () => {
    expectSignOut()
    const answer = await signOut.mutateAsync({})
    client.clear()
    if (answer.end_session) {
      postEndSession(answer.end_session)
      return
    }
    await primeCsrf()
    window.location.assign('/signed-out')
  }
}
