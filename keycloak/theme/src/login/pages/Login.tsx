import { useState } from 'react'

import { choseApp, expiredHours, resetEmail } from '../lib'
import { Card, ErrorLine, Field, Input, Message, Pill, QuietLink, Text } from '../ui'
import type { PageProps } from './props'

/**
 * Sign in (login.ftl; design 001, 003 left, 005 left). One non-specific error line on a refusal,
 * and only then the way to a reset: 001 draws no link, but the flow in 002 must be reachable.
 * After the portal's session ended on its own it sends the person here with `sclu_expired` and
 * their address (005): the card says so and asks for the password alone. After a reset link was
 * asked for, Keycloak comes back to this page with its "email sent" notice, drawn as 003 left.
 */
export default function Login({ kcContext, i18n }: PageProps<'login.ftl'>) {
  const { url, realm, login, message, messagesPerField, auth } = kcContext
  // Read once, on the first render: the address is the portal's hint for this one page.
  const [hours] = useState(expiredHours)
  // P25c: back here after choosing an authenticator app on the passkey page.
  const [app] = useState(choseApp)
  const [busy, setBusy] = useState(false)

  if (message?.type === 'success' && message.summary === i18n.msgStr('emailSentMessage')) {
    return <LinkSent kcContext={kcContext} />
  }

  const refused = messagesPerField.existsError('username', 'password')
  const knownEmail = (hours !== null && login.username) || null
  const hidden = kcContext.usernameHidden === true
  // A notice that is not about the fields (a timed-out sign-in, say) reads above them.
  const notice = !refused && message ? message : null

  return (
    <Card kcContext={kcContext} title={hours !== null ? 'Sign in again' : 'Sign in'}>
      <form
        action={url.loginAction}
        method="post"
        className="flex flex-col gap-4"
        onSubmit={() => setBusy(true)}
      >
        {hours !== null ? (
          <Text>
            You were signed out after {hours} {hours === 1 ? 'hour' : 'hours'}. Your drafts were
            saved.
          </Text>
        ) : app ? (
          <Text>Sign in again to set up your authenticator app.</Text>
        ) : null}
        {notice ? (
          <Text role={notice.type === 'error' ? 'alert' : 'status'} ink={notice.type === 'error'}>
            <Message summary={notice.summary} />
          </Text>
        ) : null}
        {hidden && auth?.attemptedUsername ? (
          <Text>
            Signing in as <span className="text-ink">{auth.attemptedUsername}</span>.
          </Text>
        ) : null}
        {knownEmail || hidden ? (
          knownEmail ? (
            <input type="hidden" name="username" value={knownEmail} autoComplete="username" />
          ) : null
        ) : (
          <Field label="Email">
            <Input
              type="email"
              name="username"
              autoComplete="username"
              required
              autoFocus={!login.username}
              defaultValue={login.username ?? ''}
            />
          </Field>
        )}
        <Field label="Password">
          <Input
            type="password"
            name="password"
            autoComplete="current-password"
            required
            autoFocus={Boolean(knownEmail || hidden || login.username)}
            invalid={refused}
            aria-describedby={refused ? 'sign-in-error' : undefined}
          />
        </Field>
        {refused ? (
          <ErrorLine id="sign-in-error">
            {messagesPerField.getFirstError('username', 'password')}
          </ErrorLine>
        ) : null}
        <input type="hidden" name="credentialId" value={kcContext.auth?.selectedCredential ?? ''} />
        <Pill id="kc-login" busy={busy}>
          {busy ? 'Signing in…' : hours !== null ? 'Sign in again' : 'Sign in'}
        </Pill>
        {refused && realm.resetPasswordAllowed ? (
          <QuietLink href={url.loginResetCredentialsUrl}>Forgot password</QuietLink>
        ) : null}
        {hidden ? <QuietLink href={url.loginRestartFlowUrl}>Not you? Start again</QuietLink> : null}
      </form>
    </Card>
  )
}

/** 003 left: the same answer whether or not the address is anyone's. */
function LinkSent({ kcContext }: Pick<PageProps<'login.ftl'>, 'kcContext'>) {
  const email = kcContext.login.username || resetEmail.get()
  // Keycloak's lifespan for a link a person asked for themselves (the realm's setting).
  const realm = kcContext.realm as { resetCredentialsActionTokenLifespanMinutes?: number }
  const minutes = realm.resetCredentialsActionTokenLifespanMinutes ?? 30
  return (
    <Card kcContext={kcContext} title="Reset link sent">
      <Text role="status">
        If an account uses {email ? <span className="text-ink">{email}</span> : 'that email'}, a
        reset link is on its way. It works for {minutes} {minutes === 1 ? 'minute' : 'minutes'}.
      </Text>
      <QuietLink href={kcContext.url.loginUrl}>Back to sign in</QuietLink>
    </Card>
  )
}
