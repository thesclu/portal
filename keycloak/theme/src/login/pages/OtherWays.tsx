import type { KcContext } from '../KcContext'
import { PostLink } from '../ui'

export type Way = 'passkey' | 'recovery' | 'app' | 'other'

type Selection = { authExecId: string; displayName: string }

/** Which way of signing in a Keycloak "authentication selection" is. */
export function wayOf(selection: Selection): Way {
  if (selection.displayName.startsWith('webauthn')) return 'passkey'
  if (selection.displayName.startsWith('recovery-authn-codes')) return 'recovery'
  if (selection.displayName.startsWith('otp') || selection.displayName.startsWith('auth-otp')) {
    return 'app'
  }
  return 'other'
}

export const WAY_WORDS: Record<Way, string> = {
  passkey: 'Use your passkey',
  recovery: 'Use a recovery code',
  app: 'Use your authenticator app',
  other: 'Try another way',
}

/**
 * The quiet link under a second step's button. With one other way (a passkey and recovery codes,
 * the usual pair) it goes straight there, naming it; with more it opens Keycloak's list of ways
 * (select-authenticator.ftl). Both post to the page's own action, as Keycloak's list does.
 */
export function OtherWays({ kcContext, current }: { kcContext: KcContext; current: Way }) {
  const auth = kcContext.auth as { authenticationSelections?: Selection[] } | undefined
  const others = (auth?.authenticationSelections ?? []).filter((s) => wayOf(s) !== current)
  const action = kcContext.url.loginAction
  if (others.length === 0 || !action) return null
  if (others.length === 1) {
    return (
      <PostLink action={action} fields={{ authenticationExecution: others[0].authExecId }}>
        {WAY_WORDS[wayOf(others[0])]}
      </PostLink>
    )
  }
  return (
    <PostLink action={action} fields={{ tryAnotherWay: 'on' }}>
      Try another way
    </PostLink>
  )
}
