import { useState } from 'react'

import { inviteFor, type Invite } from '../lib'
import {
  Card,
  Checkbox,
  ErrorLine,
  Field,
  Input,
  Pill,
  QuietButton,
  ReadonlyBox,
  Text,
} from '../ui'
import type { PageProps } from './props'

/**
 * A new password (login-update-password.ftl), three ways:
 *  - invite (design 002 left): the person followed an invite email; who invited them and as what
 *    come from that email's link (lib.ts, rememberInvite). One password field: Keycloak's
 *    confirmation is filled from it. The drawn Name field is left out: the portal's own new-user
 *    setup asks for the name after the first sign-in.
 *  - change: the portal's Settings asked for it (an application-initiated action), so the person
 *    may cancel and may sign out their other devices.
 *  - reset (design 003 right): the forgot-password link, an admin's "set a new password" email,
 *    or a password an admin marked to be changed at the next sign-in.
 */
export default function LoginUpdatePassword({ kcContext }: PageProps<'login-update-password.ftl'>) {
  const email = (kcContext as { username?: string }).username
  const change = kcContext.isAppInitiatedAction === true
  const invite = change ? null : inviteFor(email)
  return invite ? (
    <InviteForm kcContext={kcContext} invite={invite} email={email ?? ''} />
  ) : (
    <NewPasswordForm kcContext={kcContext} change={change} email={email} />
  )
}

type Props = Pick<PageProps<'login-update-password.ftl'>, 'kcContext'>

/** Keycloak's refusal of the password: under the field it names, or (a policy it failed, such
 *  as the length) as the page's error. */
function serverError(kcContext: Props['kcContext']): string | null {
  const { messagesPerField, message } = kcContext
  if (messagesPerField.existsError('password', 'password-confirm')) {
    return messagesPerField.getFirstError('password', 'password-confirm')
  }
  return message?.type === 'error' ? message.summary : null
}

/** The password manager's hint: whose password this is. Keycloak's own page carries the same. */
function Username({ email }: { email?: string }) {
  return email ? (
    <input
      type="text"
      name="username"
      value={email}
      autoComplete="username"
      readOnly
      hidden
      aria-hidden
    />
  ) : null
}

function InviteForm({ kcContext, invite, email }: Props & { invite: Invite; email: string }) {
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const error = serverError(kcContext)
  const length =
    (kcContext as { passwordPolicies?: { length?: number } }).passwordPolicies?.length ?? 12
  return (
    <Card kcContext={kcContext} title="Accept invite">
      <form
        action={kcContext.url.loginAction}
        method="post"
        className="flex flex-col gap-4"
        onSubmit={() => setBusy(true)}
      >
        <Text>
          {invite.invitedBy} invited you
          {invite.role ? (
            <>
              {' as '}
              <span className="font-medium text-ink">{invite.role}</span>
            </>
          ) : null}
          .
        </Text>
        <div className="flex flex-col gap-1.5">
          <span className="text-125 text-ink-2">Email</span>
          <ReadonlyBox>{email}</ReadonlyBox>
        </div>
        <Username email={email} />
        <Field label="Choose a password" hint={error ? undefined : `At least ${length} characters`}>
          <Input
            type="password"
            name="password-new"
            autoComplete="new-password"
            required
            minLength={length}
            autoFocus
            value={password}
            invalid={Boolean(error)}
            aria-describedby={error ? 'password-error' : undefined}
            onChange={(event) => setPassword(event.target.value)}
          />
        </Field>
        <input type="hidden" name="password-confirm" value={password} />
        {error ? <ErrorLine id="password-error">{error}</ErrorLine> : null}
        <Pill busy={busy}>{busy ? 'Creating…' : 'Create account'}</Pill>
      </form>
    </Card>
  )
}

function NewPasswordForm({
  kcContext,
  change,
  email,
}: Props & { change: boolean; email?: string }) {
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const mismatch = confirm.length > 0 && confirm !== password
  const server = serverError(kcContext)
  const error = mismatch ? 'The two passwords differ.' : server
  const passwordInvalid =
    !mismatch && Boolean(server) && !kcContext.messagesPerField.existsError('password-confirm')
  return (
    <Card kcContext={kcContext} title={change ? 'Change password' : 'Choose a new password'}>
      <form
        action={kcContext.url.loginAction}
        method="post"
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          // Cancel posts the form too; only a save waits on Keycloak's answer.
          const submitter = (event.nativeEvent as SubmitEvent).submitter
          if (submitter?.getAttribute('name') !== 'cancel-aia') setBusy(true)
        }}
      >
        <Username email={email} />
        <Field label="New password">
          <Input
            type="password"
            name="password-new"
            autoComplete="new-password"
            required
            autoFocus
            value={password}
            invalid={passwordInvalid}
            aria-describedby={passwordInvalid ? 'password-error' : undefined}
            onChange={(event) => setPassword(event.target.value)}
          />
        </Field>
        <Field label="Confirm it">
          <Input
            type="password"
            name="password-confirm"
            autoComplete="new-password"
            required
            value={confirm}
            invalid={mismatch || kcContext.messagesPerField.existsError('password-confirm')}
            aria-describedby={error ? 'password-error' : undefined}
            onChange={(event) => setConfirm(event.target.value)}
          />
        </Field>
        {error ? <ErrorLine id="password-error">{error}</ErrorLine> : null}
        {change ? (
          <Checkbox name="logout-sessions" value="on" label="Sign out other devices" />
        ) : null}
        <Pill busy={busy} disabled={mismatch}>
          {busy ? 'Saving…' : change ? 'Change password' : 'Save and sign in'}
        </Pill>
        {change ? (
          <QuietButton type="submit" name="cancel-aia" value="true" formNoValidate>
            Cancel
          </QuietButton>
        ) : null}
      </form>
    </Card>
  )
}
