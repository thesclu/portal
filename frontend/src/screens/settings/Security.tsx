import { useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router'

import type { Schemas } from '../../api/client'
import { $api, messageOf } from '../../api/query'
import { useMe } from '../../app/session'
import { Dialog } from '../../components/Dialog'
import { Button } from '../../components/ui'
import { civilDayOf, formatShortDate, formatShortInstant } from '../../lib/dates'
import { signInAddress } from '../../lib/signIn'
import { Row, RowAction, SettingsCard } from './rows'

type Security = Schemas['SecurityOut']
type Credential = Schemas['CredentialOut']

const BACK = '/settings/profile'

/** One of Keycloak's own credential pages (an application-initiated action), and back here. */
function goToKeycloak(action: string) {
  window.location.assign(signInAddress({ next: BACK, action }))
}

/**
 * Settings → Profile → Security (200): the password (Change) and two-step sign-in (Manage).
 * P25a: Keycloak holds both. What the person has is read from it (labels and dates, never a
 * secret); every change happens on Keycloak's own pages, themed to the design, and comes back
 * here: a new password, a passkey added or removed, new recovery codes, an authenticator app
 * (while an administrator allows them: on by default since P25c). Everyone has a second step,
 * a passkey or an authenticator app (owner, 2026-10-20), so there is no "Turn off".
 */
export function Security() {
  const me = useMe()
  const [params] = useSearchParams()
  const security = $api.useQuery('get', '/api/core/auth/security', {}, { retry: false })
  const [managing, setManaging] = useState(false)
  const done = params.get('done')
  const data = security.data
  const passkeys = data?.passkeys.length ?? 0
  return (
    <SettingsCard title="Security">
      {done ? (
        <p
          role="status"
          className="border-t border-line-row px-[22px] py-[11px] text-125 text-ink-2"
        >
          {done === 'success'
            ? 'Saved.'
            : done === 'cancelled'
              ? 'Nothing was changed.'
              : 'That did not go through. Try again.'}
        </p>
      ) : null}
      {security.isError ? (
        <p
          role="alert"
          className="border-t border-line-row px-[22px] py-[11px] text-125 text-danger"
        >
          {messageOf(security.error, 'Keycloak could not be reached just now.')}
        </p>
      ) : null}
      <Row
        label="Password"
        // P23b (200 "Changed Jun 2"): when it was last set, as Keycloak keeps it.
        value={
          data?.password_changed_at
            ? `Changed ${formatShortDate(civilDayOf(data.password_changed_at, me.timezone))}`
            : undefined
        }
        action={<RowAction onClick={() => goToKeycloak('UPDATE_PASSWORD')}>Change</RowAction>}
      />
      <Row
        label="Two-step sign-in"
        hint={
          data
            ? passkeys > 0
              ? passkeys === 1
                ? 'Passkey'
                : `${passkeys} passkeys`
              : data.authenticator_app
                ? 'Authenticator app'
                : 'Asked for at your next sign-in'
            : undefined
        }
        value={data ? (passkeys > 0 || data.authenticator_app ? 'On' : 'Not set up') : undefined}
        action={
          <RowAction disabled={!data} onClick={() => setManaging(true)}>
            Manage
          </RowAction>
        }
      />
      {managing && data ? <Manage security={data} onClose={() => setManaging(false)} /> : null}
    </SettingsCard>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-2">
      <span className="text-125 text-ink-2">{title}</span>
      {children}
    </div>
  )
}

function CredentialLine({
  credential,
  fallback,
  timezone,
  action,
}: {
  credential: Credential
  fallback: string
  timezone: string
  action?: ReactNode
}) {
  return (
    <div className="flex items-center gap-3 rounded-8 bg-page px-3 py-2">
      <div className="min-w-0 flex-1">
        <div className="truncate text-13 text-ink">{credential.label || fallback}</div>
        {credential.created_at ? (
          <div className="text-12 text-muted">
            Added {formatShortInstant(credential.created_at, timezone)}
          </div>
        ) : null}
      </div>
      {action}
    </div>
  )
}

/**
 * Two-step sign-in (222's dialog): the passkeys (each with Remove), Add a passkey, the recovery
 * codes (how many are left, Make new codes) and, when an administrator allows them, an
 * authenticator app. Each button opens Keycloak's page for it; Keycloak asks for the passkey again
 * first when the last check is more than a few minutes old.
 */
function Manage({ security, onClose }: { security: Security; onClose: () => void }) {
  const me = useMe()
  const since = security.passkeys[0]?.created_at
  const subtitle =
    security.passkeys.length > 0
      ? `On · passkey${since ? ` since ${formatShortInstant(since, me.timezone)}` : ''}`
      : 'Asked for at your next sign-in'
  const codes = security.recovery_codes
  const app = security.authenticator_app
  return (
    <Dialog
      title="Two-step sign-in"
      subtitle={subtitle}
      open
      onClose={onClose}
      width={480}
      top={90}
      footer={
        <Button kind="primary" pill onClick={onClose}>
          Done
        </Button>
      }
    >
      <Section title="Passkeys">
        {security.passkeys.map((passkey) => (
          <CredentialLine
            key={passkey.id}
            credential={passkey}
            fallback="Passkey"
            timezone={me.timezone}
            action={
              <Button kind="text" onClick={() => goToKeycloak(`delete_credential:${passkey.id}`)}>
                Remove
              </Button>
            }
          />
        ))}
        <div>
          <Button kind="text" onClick={() => goToKeycloak('webauthn-register')}>
            Add a passkey
          </Button>
        </div>
      </Section>
      <Section title="Recovery codes">
        <p className="text-13 leading-body text-ink-2">
          {codes
            ? `${codes.remaining ?? 'Some'} left. Each signs you in once if you lose your passkey or app.`
            : 'None yet. Each code signs you in once if you lose your passkey or app.'}
        </p>
        <div>
          <Button kind="text" onClick={() => goToKeycloak('CONFIGURE_RECOVERY_AUTHN_CODES')}>
            Make new codes
          </Button>
        </div>
      </Section>
      {security.authenticator_app_allowed || app ? (
        <Section title="Authenticator app">
          {app ? (
            <CredentialLine
              credential={app}
              fallback="Authenticator app"
              timezone={me.timezone}
              action={
                <Button kind="text" onClick={() => goToKeycloak(`delete_credential:${app.id}`)}>
                  Remove
                </Button>
              }
            />
          ) : (
            <div>
              <Button kind="text" onClick={() => goToKeycloak('CONFIGURE_TOTP')}>
                Set up
              </Button>
            </div>
          )}
        </Section>
      ) : null}
      <p className="text-12 text-muted">
        The portal asks for your second step again every {security.reverify_hours}{' '}
        {security.reverify_hours === 1 ? 'hour' : 'hours'}.
      </p>
    </Dialog>
  )
}
