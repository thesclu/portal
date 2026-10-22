import { actionToken, rememberInvite } from '../lib'
import { Card, Message, QuietLink, Text } from '../ui'
import type { PageProps } from './props'

/** Where "Back to sign in" goes: the portal, which starts a fresh sign-in. Keycloak is served
 *  under the portal's own address, so its root is the portal. */
function portalHome(baseUrl: string | undefined): string {
  return baseUrl || `${window.location.origin}/`
}

/**
 * Keycloak's error page (error.ftl). An emailed link that has expired or was used reads as 005
 * right ("Invite link expired"), without a date: Keycloak does not say when it expired. Which kind
 * of link it was comes from the link's own token; an invite is known by the fragment its email
 * adds (lib.ts). Any other error is Keycloak's own words (in the house's voice, i18n.ts).
 */
export default function ErrorPage({ kcContext, i18n }: PageProps<'error.ftl'>) {
  const { message, client, skipLink } = kcContext
  const token = actionToken()
  const expired = [
    'expiredActionMessage',
    'expiredActionTokenNoSessionMessage',
    'expiredActionTokenSessionExistsMessage',
    'invalidCodeMessage',
  ].some((key) => message.summary === i18n.msgStr(key as 'expiredActionMessage'))
  const onLink = window.location.pathname.endsWith('/login-actions/action-token')
  let words: string | null = null
  if (onLink && expired) {
    if (token?.type === 'reset-credentials') {
      words = 'This reset link has expired. Ask for a new one.'
    } else if (token && rememberInvite(token.email)) {
      words = 'This invite link has expired. Ask an admin to send a new one.'
    } else if (token) {
      words = 'This link has expired. Ask an admin to send a new one.'
    } else {
      words = 'This link cannot be used. Ask an admin to send a new one.'
    }
  }
  return (
    <Card kcContext={kcContext} title={words ? 'Link expired' : 'Something went wrong'}>
      <Text ink role="alert">
        {words ?? <Message summary={message.summary} />}
      </Text>
      {skipLink ? null : <QuietLink href={portalHome(client?.baseUrl)}>Back to sign in</QuietLink>}
    </Card>
  )
}
