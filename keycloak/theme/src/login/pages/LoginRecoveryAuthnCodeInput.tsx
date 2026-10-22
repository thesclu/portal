import { useState } from 'react'

import { Card, ErrorLine, Field, Input, Pill, Text } from '../ui'
import { OtherWays } from './OtherWays'
import type { PageProps } from './props'

/**
 * A recovery code in place of the passkey (login-recovery-authn-code-input.ftl), as the portal's
 * backup-code mode of 004 right: one line, one field, Verify, and the way back to the passkey.
 * Keycloak asks for the codes in their saved order, so the page names the one it wants.
 */
export default function LoginRecoveryAuthnCodeInput({
  kcContext,
}: PageProps<'login-recovery-authn-code-input.ftl'>) {
  const { url, messagesPerField, recoveryAuthnCodesInputBean } = kcContext
  const [busy, setBusy] = useState(false)
  const number = recoveryAuthnCodesInputBean.codeNumber
  const invalid = messagesPerField.existsError('recoveryCodeInput')
  return (
    <Card kcContext={kcContext} title="Use a recovery code">
      <form
        action={url.loginAction}
        method="post"
        className="flex flex-col gap-4"
        onSubmit={() => setBusy(true)}
      >
        <Text>Enter recovery code {number} from the list you saved. Each code works once.</Text>
        <Field label={`Recovery code ${number}`}>
          <Input
            name="recoveryCodeInput"
            autoComplete="one-time-code"
            autoCapitalize="characters"
            spellCheck={false}
            required
            autoFocus
            invalid={invalid}
            aria-describedby={invalid ? 'code-error' : undefined}
          />
        </Field>
        {invalid ? (
          <ErrorLine id="code-error">{messagesPerField.get('recoveryCodeInput')}</ErrorLine>
        ) : null}
        <Pill id="kc-login" busy={busy}>
          {busy ? 'Checking…' : 'Verify'}
        </Pill>
        <OtherWays kcContext={kcContext} current="recovery" />
      </form>
    </Card>
  )
}
