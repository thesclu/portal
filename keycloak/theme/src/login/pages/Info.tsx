import { actionToken, rememberInvite } from '../lib'
import { Card, Message, PillLink, Text } from '../ui'
import type { PageProps } from './props'

/** What each of Keycloak's required actions asks of the person, in plain words. */
const ASKS: Record<string, string> = {
  UPDATE_PASSWORD: 'choose a new password',
  'webauthn-register': 'add a passkey',
  CONFIGURE_RECOVERY_AUTHN_CODES: 'save recovery codes',
  CONFIGURE_TOTP: 'set up an authenticator app',
  VERIFY_EMAIL: 'confirm your email',
  UPDATE_PROFILE: 'check your details',
  UPDATE_EMAIL: 'change your email',
  TERMS_AND_CONDITIONS: 'accept the terms',
}

function sentence(parts: string[]): string {
  return parts.length <= 1
    ? (parts[0] ?? '')
    : `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}`
}

/**
 * Keycloak's notices (info.ftl):
 *  - an emailed link opened (Keycloak asks before it acts, so a link checker cannot use it up):
 *    an invite reads as 002 left's line and Continue; an admin's other requests say what they ask;
 *  - "your account is up to date" after those steps, with the way on to the portal;
 *  - any other notice, in the card.
 */
export default function Info({ kcContext }: PageProps<'info.ftl'>) {
  const { message, requiredActions, skipLink, pageRedirectUri, actionUri, client } = kcContext
  if (requiredActions?.length && actionUri) {
    const token = actionToken()
    const invite = rememberInvite(token?.email ?? null)
    const asks = requiredActions.map((action) => ASKS[action]).filter(Boolean)
    return (
      <Card kcContext={kcContext} title={invite ? 'Accept invite' : 'Continue'}>
        <Text>
          {invite ? (
            <>
              {invite.invitedBy} invited you
              {invite.role ? (
                <>
                  {' as '}
                  <span className="font-medium text-ink">{invite.role}</span>
                </>
              ) : null}
              .
            </>
          ) : asks.length ? (
            `An admin asked you to ${sentence(asks)}.`
          ) : (
            'An admin asked you to update your account.'
          )}
        </Text>
        <PillLink href={actionUri} data-continue="">
          Continue
        </PillLink>
      </Card>
    )
  }
  const next = skipLink ? null : (pageRedirectUri ?? actionUri ?? client?.baseUrl ?? null)
  return (
    <Card kcContext={kcContext} title="SCLU Portal">
      <Text role="status">
        <Message summary={message.summary} />
      </Text>
      {next ? (
        <PillLink href={next} data-continue="">
          Continue
        </PillLink>
      ) : null}
    </Card>
  )
}
