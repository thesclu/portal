import { useState } from 'react'

import { Card, CodeBoxes, ErrorLine, Pill, Text, cx } from '../ui'
import { OtherWays } from './OtherWays'
import type { PageProps } from './props'

/**
 * A code from the authenticator app (login-otp.ftl; design 004 right): the six boxes, Verify, and
 * the quiet way to a recovery code (or the list of ways, for someone with a passkey too). While
 * an admin allows authenticator apps (Settings; on by default since P25c).
 */
export default function LoginOtp({ kcContext }: PageProps<'login-otp.ftl'>) {
  const { url, otpLogin, messagesPerField } = kcContext
  const [busy, setBusy] = useState(false)
  const [chosen, setChosen] = useState(
    otpLogin.selectedCredentialId ?? otpLogin.userOtpCredentials[0]?.id ?? '',
  )
  const invalid = messagesPerField.existsError('totp')
  return (
    <Card kcContext={kcContext} title="Enter your code">
      <form
        action={url.loginAction}
        method="post"
        className="flex flex-col gap-4"
        onSubmit={() => setBusy(true)}
      >
        <Text>Enter the 6-digit code from your authenticator app.</Text>
        <input type="hidden" name="selectedCredentialId" value={chosen} />
        {otpLogin.userOtpCredentials.length > 1 ? (
          // More than one app: which one the code is from.
          <div
            role="radiogroup"
            aria-label="Authenticator app"
            className="flex flex-wrap justify-center gap-2"
          >
            {otpLogin.userOtpCredentials.map((credential) => (
              <button
                key={credential.id}
                type="button"
                role="radio"
                aria-checked={credential.id === chosen}
                onClick={() => setChosen(credential.id)}
                className={cx(
                  'h-7 rounded-pill px-[11px] text-115 font-medium',
                  credential.id === chosen
                    ? 'bg-brand text-white'
                    : 'border border-line-2 text-ink-2',
                )}
              >
                {credential.userLabel}
              </button>
            ))}
          </div>
        ) : null}
        <CodeBoxes name="otp" invalid={invalid} describedBy={invalid ? 'otp-error' : undefined} />
        {invalid ? <ErrorLine id="otp-error">{messagesPerField.get('totp')}</ErrorLine> : null}
        <Pill id="kc-login" busy={busy}>
          {busy ? 'Checking…' : 'Verify'}
        </Pill>
        <OtherWays kcContext={kcContext} current="app" />
      </form>
    </Card>
  )
}
