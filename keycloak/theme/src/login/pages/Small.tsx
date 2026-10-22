/**
 * The rest of Keycloak's pages the portal's people can meet, each in the same card: a page left
 * open too long, removing a passkey from Settings, signing out, and a passkey that did not answer.
 */
import { useState } from 'react'

import { Card, Message, Pill, PillLink, QuietButton, QuietLink, Text } from '../ui'
import type { PageProps } from './props'

/** login-page-expired.ftl: carry on where the person was, or start again. */
export function LoginPageExpired({ kcContext }: PageProps<'login-page-expired.ftl'>) {
  const { url } = kcContext
  return (
    <Card kcContext={kcContext} title="Page expired">
      <Text>This page was open too long.</Text>
      <PillLink href={url.loginAction}>Continue signing in</PillLink>
      <QuietLink href={url.loginRestartFlowUrl}>Start again</QuietLink>
    </Card>
  )
}

/** delete-credential.ftl: Settings asked to remove a passkey (or the app, or the codes). */
export function DeleteCredential({ kcContext }: PageProps<'delete-credential.ftl'>) {
  const { url, credentialLabel } = kcContext
  const [busy, setBusy] = useState(false)
  return (
    <Card kcContext={kcContext} title="Remove from two-step sign-in?">
      <form
        action={url.loginAction}
        method="post"
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          const submitter = (event.nativeEvent as SubmitEvent).submitter
          if (submitter?.getAttribute('name') === 'accept') setBusy(true)
        }}
      >
        <Text>
          Remove <span className="text-ink">{credentialLabel}</span>? It will no longer sign you in.
        </Text>
        <Pill name="accept" value="true" busy={busy} className="bg-danger hover:bg-danger-2">
          {busy ? 'Removing…' : 'Remove'}
        </Pill>
        <QuietButton type="submit" name="cancel-aia" value="true">
          Cancel
        </QuietButton>
      </form>
    </Card>
  )
}

/** logout-confirm.ftl: a sign-out that did not come with the portal's proof, so Keycloak asks. */
export function LogoutConfirm({ kcContext }: PageProps<'logout-confirm.ftl'>) {
  const { url, logoutConfirm, client } = kcContext
  const [busy, setBusy] = useState(false)
  return (
    <Card kcContext={kcContext} title="Sign out">
      <form
        action={url.logoutConfirmAction}
        method="post"
        className="flex flex-col gap-4"
        onSubmit={() => setBusy(true)}
      >
        <Text>Sign out of SCLU Portal?</Text>
        <input type="hidden" name="session_code" value={logoutConfirm.code} />
        <Pill name="confirmLogout" value="true" busy={busy}>
          {busy ? 'Signing out…' : 'Sign out'}
        </Pill>
        {!logoutConfirm.skipLink && client.baseUrl ? (
          <QuietLink href={client.baseUrl}>Back to SCLU Portal</QuietLink>
        ) : null}
      </form>
    </Card>
  )
}

/** webauthn-error.ftl: the passkey prompt was closed or failed. Try again (or cancel, from
 *  Settings). */
export function WebauthnError({ kcContext }: PageProps<'webauthn-error.ftl'>) {
  const { url, message, execution, isAppInitiatedAction } = kcContext
  return (
    <Card kcContext={kcContext} title="Passkey">
      <Text ink role="alert">
        {message ? <Message summary={message.summary} /> : 'The passkey was not used.'}
      </Text>
      <form action={url.loginAction} method="post" className="flex flex-col gap-4">
        <input type="hidden" name="authenticationExecution" value={execution} />
        <input type="hidden" name="isSetRetry" value="retry" />
        <Pill>Try again</Pill>
      </form>
      {isAppInitiatedAction ? (
        <form action={url.loginAction} method="post" className="contents">
          <QuietButton type="submit" name="cancel-aia" value="true">
            Cancel
          </QuietButton>
        </form>
      ) : null}
    </Card>
  )
}
