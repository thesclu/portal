import { useScript } from 'keycloakify/login/pages/WebauthnAuthenticate.useScript'
import { useState } from 'react'

import { Card, Pill, Text } from '../ui'
import { OtherWays } from './OtherWays'
import type { PageProps } from './props'

const BUTTON = 'authenticateWebAuthnButton'

/**
 * The passkey check (webauthn-authenticate.ftl): the second step of every sign-in, and the
 * periodic re-check, which shows this page alone. Not drawn: built in 004 right's layout
 * ("Entering a two-step code") with "Use passkey" in place of the code boxes, and under it the
 * quiet way to a recovery code (or to the list, when there is more than one other way).
 */
export default function WebauthnAuthenticate({
  kcContext,
  i18n,
}: PageProps<'webauthn-authenticate.ftl'>) {
  const { url, authenticators, message } = kcContext
  const [waiting, setWaiting] = useState(false)
  useScript({ authButtonId: BUTTON, kcContext, i18n })
  return (
    <Card kcContext={kcContext} title="Confirm it’s you">
      <Text>Confirm it’s you with your passkey.</Text>
      {message?.type === 'error' ? (
        <Text role="alert" ink>
          {message.summary}
        </Text>
      ) : null}
      <form id="webauth" action={url.loginAction} method="post" className="contents">
        <input type="hidden" id="clientDataJSON" name="clientDataJSON" />
        <input type="hidden" id="authenticatorData" name="authenticatorData" />
        <input type="hidden" id="signature" name="signature" />
        <input type="hidden" id="credentialId" name="credentialId" />
        <input type="hidden" id="userHandle" name="userHandle" />
        <input type="hidden" id="error" name="error" />
      </form>
      {/* The passkeys this person has, which the browser is told to offer. */}
      <form id="authn_select" className="hidden">
        {(authenticators?.authenticators ?? []).map((authenticator) => (
          <input
            key={authenticator.credentialId}
            type="hidden"
            name="authn_use_chk"
            value={authenticator.credentialId}
          />
        ))}
      </form>
      <Pill type="button" id={BUTTON} busy={waiting} onClick={() => setWaiting(true)}>
        {waiting ? 'Waiting for your passkey…' : 'Use passkey'}
      </Pill>
      <OtherWays kcContext={kcContext} current="passkey" />
    </Card>
  )
}
