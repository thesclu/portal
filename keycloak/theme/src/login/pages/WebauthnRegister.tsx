import { useScript } from 'keycloakify/login/pages/WebauthnRegister.useScript'
import { useEffect, useState } from 'react'

import { appAllowed, passkeyLabel } from '../lib'
import { Card, Checkbox, Pill, QuietButton, QuietLink, Text } from '../ui'
import type { PageProps } from './props'

const BUTTON = 'registerWebAuthn'
// The portal's sign-in again, asking for the authenticator app's setup (P25c).
const APP_INSTEAD = '/api/core/auth/oidc/login?second_step=app'

/**
 * Adding a passkey (webauthn-register.ftl). Not drawn: built in 004 left's layout ("Setting up
 * two-step sign-in"): the logo, one line of explanation, the action. At the first sign-in it is
 * the second step being set up; from Settings (an application-initiated action) it adds another
 * passkey, and then the person may cancel and may sign out their other devices.
 *
 * P25c: setting up the first second step for the portal, while an administrator allows
 * authenticator apps (on by default), this is the choice: the passkey first, as the page's
 * action, and "Use an authenticator app instead" under it. Keycloak cannot offer a credential the
 * person does not have yet on its own list of ways, so the choice is a sign-in again that asks for
 * the app (the portal adds the realm's `sclu-app-second-step` scope; the flow's "first app"
 * branch sets it up, then recovery codes); the sign-in page then says why it asks for the
 * password again.
 *
 * Keycloak's own script does the WebAuthn call and posts the form below. It would then ask the
 * person to type a name for the passkey in a browser prompt; the page names it after this
 * browser and system instead ("Chrome on macOS"), which Settings can show and change.
 */
export default function WebauthnRegister({ kcContext, i18n }: PageProps<'webauthn-register.ftl'>) {
  const { url, isSetRetry, isAppInitiatedAction, client } = kcContext
  const [waiting, setWaiting] = useState(false)
  const adding = isAppInitiatedAction === true
  const offerApp = !adding && client?.clientId === 'portal'
  const [app, setApp] = useState(false)
  useScript({ authButtonId: BUTTON, kcContext, i18n })
  useEffect(() => {
    const prompt = window.prompt
    window.prompt = () => passkeyLabel()
    return () => {
      window.prompt = prompt
    }
  }, [])
  useEffect(() => {
    if (!offerApp) return
    let live = true
    void appAllowed().then((allowed) => {
      if (live) setApp(allowed)
    })
    return () => {
      live = false
    }
  }, [offerApp])
  return (
    <Card kcContext={kcContext} title={app ? 'Set up two-step sign-in' : 'Add a passkey'}>
      <Text>
        {adding
          ? 'Add a passkey to sign in with. Your fingerprint, face or phone will confirm it’s you.'
          : app
            ? 'Add a passkey. Your fingerprint, face or phone will confirm it’s you each time you sign in.'
            : 'Add a passkey to finish setting up two-step sign-in. Your fingerprint, face or phone will confirm it’s you.'}
      </Text>
      <form id="register" action={url.loginAction} method="post" className="contents">
        <input type="hidden" id="clientDataJSON" name="clientDataJSON" />
        <input type="hidden" id="attestationObject" name="attestationObject" />
        <input type="hidden" id="publicKeyCredentialId" name="publicKeyCredentialId" />
        <input type="hidden" id="authenticatorLabel" name="authenticatorLabel" />
        <input type="hidden" id="transports" name="transports" />
        <input type="hidden" id="authenticatorAttachment" name="authenticatorAttachment" />
        <input type="hidden" id="error" name="error" />
        {adding ? (
          <Checkbox name="logout-sessions" value="on" label="Sign out other devices" />
        ) : null}
      </form>
      <Pill type="button" id={BUTTON} busy={waiting} onClick={() => setWaiting(true)}>
        {waiting ? 'Waiting for your passkey…' : 'Add passkey'}
      </Pill>
      {app && !waiting ? (
        <QuietLink href={APP_INSTEAD} data-second-step="app">
          Use an authenticator app instead
        </QuietLink>
      ) : null}
      {adding && !isSetRetry ? (
        <form action={url.loginAction} method="post" className="contents">
          <QuietButton type="submit" name="cancel-aia" value="true">
            Cancel
          </QuietButton>
        </form>
      ) : null}
    </Card>
  )
}
