import { useState } from 'react'

import { resetEmail } from '../lib'
import { Card, ErrorLine, Field, Input, Message, Pill, QuietLink, Text } from '../ui'
import type { PageProps } from './props'

/**
 * Forgot password (login-reset-password.ftl; design 002 right). The address, then Keycloak's
 * answer, which is the same whether or not the address is anyone's (the sign-in page with its
 * "email sent" notice, drawn as 003 left by Login). The address is kept for this tab so that
 * notice can name it.
 */
export default function LoginResetPassword({ kcContext }: PageProps<'login-reset-password.ftl'>) {
  const { url, auth, message, messagesPerField } = kcContext
  const [busy, setBusy] = useState(false)
  const invalid = messagesPerField.existsError('username')
  const notice = !invalid && message && message.type !== 'warning' ? message : null
  return (
    <Card kcContext={kcContext} title="Forgot password">
      <form
        action={url.loginAction}
        method="post"
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          const field = event.currentTarget.elements.namedItem('username')
          if (field instanceof HTMLInputElement) resetEmail.set(field.value)
          setBusy(true)
        }}
      >
        <Text>Enter your email and we’ll send a link to reset it.</Text>
        {notice ? (
          <Text role={notice.type === 'error' ? 'alert' : 'status'} ink={notice.type === 'error'}>
            <Message summary={notice.summary} />
          </Text>
        ) : null}
        <Field label="Email">
          <Input
            type="email"
            name="username"
            autoComplete="username"
            required
            autoFocus
            defaultValue={auth?.attemptedUsername ?? ''}
            invalid={invalid}
            aria-describedby={invalid ? 'reset-error' : undefined}
          />
        </Field>
        {invalid ? (
          <ErrorLine id="reset-error">{messagesPerField.get('username')}</ErrorLine>
        ) : null}
        <Pill busy={busy}>{busy ? 'Sending…' : 'Send reset link'}</Pill>
        <QuietLink href={url.loginUrl}>Back to sign in</QuietLink>
      </form>
    </Card>
  )
}
