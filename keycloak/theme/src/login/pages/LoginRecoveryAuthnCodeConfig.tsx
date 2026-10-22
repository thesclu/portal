import { useState } from 'react'

import { Card, Checkbox, Pill, QuietButton, Text, cx } from '../ui'
import type { PageProps } from './props'

/** 222's Download and Make new codes: text buttons, 32 px, 12.5 px 500 in ink-2. */
const TEXT_BUTTON =
  'inline-flex h-8 items-center rounded-9 px-3.5 text-125 font-medium text-ink-2 transition-colors duration-[120ms] hover:bg-hover'

/** XXXX-XXXX-XXXX, as Keycloak's own page writes them. */
const grouped = (code: string) => code.match(/.{1,4}/g)?.join('-') ?? code

/**
 * Saving the recovery codes (login-recovery-authn-code-config.ftl): the codes in 222's grid (on
 * the page grey, 12.5 px, centred), Download and Copy, Keycloak's confirmation box, Continue; and
 * Cancel when Settings asked for new codes. Keycloak's codes are 14 characters, not 222's 9 (up
 * to 148 px wide at 12.5 px), so the grid holds two to a row in the card's 360 px, not four.
 */
export default function LoginRecoveryAuthnCodeConfig({
  kcContext,
}: PageProps<'login-recovery-authn-code-config.ftl'>) {
  const { url, recoveryAuthnCodesConfigBean: bean, isAppInitiatedAction } = kcContext
  const codes = bean.generatedRecoveryAuthnCodesList.map(grouped)
  const [saved, setSaved] = useState(false)
  const [copied, setCopied] = useState(false)
  const [busy, setBusy] = useState(false)
  const adding = isAppInitiatedAction === true

  function download() {
    const text = `${kcContext.realm.displayName || 'SCLU Portal'} recovery codes\nEach code signs you in once, in this order.\n\n${codes.map((code, i) => `${i + 1}. ${code}`).join('\n')}\n`
    const link = document.createElement('a')
    link.href = URL.createObjectURL(new Blob([text], { type: 'text/plain' }))
    link.download = 'sclu-portal-recovery-codes.txt'
    link.click()
    window.setTimeout(() => URL.revokeObjectURL(link.href), 1_000)
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(codes.join('\n'))
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2_000)
    } catch {
      /* the codes stay on screen to copy by hand */
    }
  }

  return (
    <Card kcContext={kcContext} title="Save your recovery codes">
      <Text>
        Save these somewhere safe. If your passkey or authenticator app is not at hand, a code signs
        you in once. They are shown only now.
      </Text>
      <ol
        id="kc-recovery-codes-list"
        aria-label="Recovery codes"
        className="grid grid-cols-2 gap-2"
      >
        {codes.map((code) => (
          <li key={code} className="rounded-8 bg-page p-2 text-center text-125 text-ink select-all">
            {code}
          </li>
        ))}
      </ol>
      <div className="-mt-1 flex justify-center gap-2">
        <button type="button" onClick={download} className={TEXT_BUTTON}>
          Download
        </button>
        <button type="button" onClick={() => void copy()} className={TEXT_BUTTON}>
          <span aria-live="polite">{copied ? 'Copied' : 'Copy'}</span>
        </button>
      </div>
      <form
        action={url.loginAction}
        method="post"
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          const submitter = (event.nativeEvent as SubmitEvent).submitter
          if (submitter?.getAttribute('name') !== 'cancel-aia') setBusy(true)
        }}
      >
        <input
          type="hidden"
          name="generatedRecoveryAuthnCodes"
          value={bean.generatedRecoveryAuthnCodesAsString}
        />
        <input type="hidden" name="generatedAt" value={String(bean.generatedAt)} />
        <input type="hidden" name="userLabel" value="Recovery codes" />
        <Checkbox
          id="kcRecoveryCodesConfirmationCheck"
          checked={saved}
          onChange={(event) => setSaved(event.target.checked)}
          label="I saved these codes somewhere safe"
        />
        {adding ? (
          <Checkbox name="logout-sessions" value="on" label="Sign out other devices" />
        ) : null}
        <Pill
          id="saveRecoveryAuthnCodesBtn"
          busy={busy}
          disabled={!saved}
          className={cx(!saved && 'hover:bg-brand')}
        >
          {busy ? 'Saving…' : 'Continue'}
        </Pill>
        {adding ? (
          <QuietButton type="submit" name="cancel-aia" value="true" formNoValidate>
            Cancel
          </QuietButton>
        ) : null}
      </form>
    </Card>
  )
}
