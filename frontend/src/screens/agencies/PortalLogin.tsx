import { useEffect, useState } from 'react'

import { api, type Schemas } from '../../api/client'
import { messageOf } from '../../api/query'
import { unwrap, type ActionSpec, type Values } from '../../components/ActionDialog'
import { InfoRow } from '../../components/ListTable'
import { formatShortDate } from '../../lib/dates'

/**
 * The stored portal login for an agency (P19g), drawn as the Filing card's "Portal login" line
 * (167): the login, the hidden password, Reveal and Copy, and who changed it last.
 *
 * The password is held encrypted on the server and comes to the browser only through Reveal,
 * which the server records in the activity log. Here the revealed value lives in this
 * component's state alone: for twenty seconds, until Hide, until the stored login changes, or
 * until the page is left. It is never put in a title, a toast, a list or a log line.
 */
type Login = Schemas['AgencyLoginOut']

export const LOGIN_PATH = '/api/records/agencies/{agency_id}/login' as const
const REVEAL_FOR = 20_000
const text = (values: Values, name: string) => String(values[name] ?? '').trim()

export function editLogin(
  agencyId: string,
  stored: Login | null,
  onClear?: (spec: ActionSpec) => void,
): ActionSpec {
  return {
    key: `login-${agencyId}`,
    width: 460,
    compact: true,
    secondary:
      stored && onClear
        ? { label: 'Clear login', run: () => onClear(clearLogin(agencyId)) }
        : undefined,
    title: stored ? 'Edit portal login' : 'Add portal login',
    intro: 'The password is kept encrypted. Every reveal is recorded in the activity log.',
    submit: stored ? 'Save' : 'Add login',
    fields: [
      { name: 'login', label: 'Login', required: true, initial: stored?.login ?? '' },
      {
        name: 'password',
        label: 'Password',
        type: 'password',
        hint: stored?.has_password ? 'Leave empty to keep the stored password.' : undefined,
      },
      {
        name: 'remove_password',
        label: 'Remove the stored password',
        type: 'checkbox',
        when: (values) => Boolean(stored?.has_password) && !text(values, 'password'),
      },
      { name: 'notes', label: 'Notes', type: 'textarea', initial: stored?.notes ?? '' },
    ],
    run: async (values) => {
      const password = String(values.password ?? '')
      const body: Schemas['AgencyLoginIn'] = {
        login: text(values, 'login'),
        notes: text(values, 'notes') || null,
      }
      // Left out, the server keeps the stored password; an empty string removes it.
      if (password) body.password = password
      else if (values.remove_password === true) body.password = ''
      unwrap(await api.PUT(LOGIN_PATH, { params: { path: { agency_id: agencyId } }, body }))
    },
  }
}

export function clearLogin(agencyId: string): ActionSpec {
  return {
    key: `clear-login-${agencyId}`,
    title: 'Clear portal login',
    intro: 'The stored login and its password are removed. This cannot be undone.',
    submit: 'Clear login',
    danger: true,
    fields: [],
    run: async () => {
      unwrap(await api.DELETE(LOGIN_PATH, { params: { path: { agency_id: agencyId } } }))
    },
  }
}

export function PortalLoginRow({
  agencyId,
  stored,
  onAction,
}: {
  agencyId: string
  /** What GET /agencies/{id}/login answered: null when nothing is stored. */
  stored: Login | null
  onAction: (spec: ActionSpec) => void
}) {
  // The revealed value remembers which stored login it belongs to: an edit or a clear changes
  // `changed_at`, and a value revealed for the old one is not shown for the new one.
  const changedAt = stored?.changed_at ?? null
  const [revealed, setShown] = useState<{
    password: string
    until: number
    changedAt: string | null
  } | null>(null)
  const shown = revealed && revealed.changedAt === changedAt ? revealed : null
  const [now, setNow] = useState(() => Date.now())
  const [busy, setBusy] = useState<'reveal' | 'copy' | null>(null)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Count down while revealed, and drop the value when the time is up.
  useEffect(() => {
    if (!shown) return
    const id = window.setInterval(() => {
      const tick = Date.now()
      if (tick >= shown.until) setShown(null)
      else setNow(tick)
    }, 250)
    return () => window.clearInterval(id)
  }, [shown])

  useEffect(() => {
    if (!copied) return
    const id = window.setTimeout(() => setCopied(false), 2_000)
    return () => window.clearTimeout(id)
  }, [copied])

  // Editing or clearing replaces the stored login; what was revealed for the old one goes.
  const act = (spec: ActionSpec) => {
    setShown(null)
    setCopied(false)
    setError(null)
    onAction(spec)
  }

  async function reveal(): Promise<string> {
    const { password } = unwrap(
      await api.POST('/api/records/agencies/{agency_id}/login/reveal', {
        params: { path: { agency_id: agencyId } },
      }),
    )
    setShown({ password, until: Date.now() + REVEAL_FOR, changedAt })
    setNow(Date.now())
    return password
  }

  async function onReveal() {
    setBusy('reveal')
    setError(null)
    try {
      await reveal()
    } catch (caught) {
      setError(messageOf(caught, caught instanceof Error ? caught.message : undefined))
    } finally {
      setBusy(null)
    }
  }

  async function onCopy() {
    setBusy('copy')
    setError(null)
    try {
      const password = shown?.password ?? (await reveal())
      try {
        await navigator.clipboard.writeText(password)
        setCopied(true)
      } catch {
        setError('Could not copy. Select the revealed password and copy it by hand.')
      }
    } catch (caught) {
      setError(messageOf(caught, caught instanceof Error ? caught.message : undefined))
    } finally {
      setBusy(null)
    }
  }

  const link =
    'text-125 font-medium whitespace-nowrap text-brand hover:text-brand-dark disabled:opacity-55'
  if (!stored) {
    return (
      <InfoRow label="Portal login" className="items-center">
        <span className="text-muted">None stored</span>
        <span className="ml-auto flex gap-3">
          <button
            type="button"
            className={link}
            onClick={() => act(editLogin(agencyId, null, act))}
          >
            Add
          </button>
        </span>
      </InfoRow>
    )
  }
  return (
    <>
      <InfoRow label="Portal login" className="items-center">
        <span className="min-w-0 break-all text-ink">{stored.login}</span>
        {stored.has_password ? (
          <span
            aria-label="Password"
            className={shown ? 'min-w-0 break-all text-ink-2' : 'tracking-[.15em] text-ink-2'}
          >
            {shown ? shown.password : '••••••••'}
          </span>
        ) : (
          <span className="text-muted">no password</span>
        )}
        <span className="ml-auto flex gap-3">
          {stored.has_password ? (
            <>
              {shown ? (
                <button type="button" className={link} onClick={() => setShown(null)}>
                  Hide ({Math.max(1, Math.ceil((shown.until - now) / 1000))}s)
                </button>
              ) : (
                <button
                  type="button"
                  className={link}
                  disabled={busy === 'reveal'}
                  onClick={() => void onReveal()}
                >
                  Reveal
                </button>
              )}
              <button
                type="button"
                className={link}
                disabled={busy === 'copy'}
                onClick={() => void onCopy()}
              >
                {copied ? 'Copied' : 'Copy'}
              </button>
            </>
          ) : null}
          <button
            type="button"
            className={link}
            onClick={() => act(editLogin(agencyId, stored, act))}
          >
            Edit
          </button>
        </span>
      </InfoRow>
      {stored.changed_at || error || stored.notes ? (
        <div className="-mt-px flex flex-col gap-0.5 pr-5 pb-[11px] pl-[154px] text-115 leading-[normal] text-muted">
          {stored.changed_at ? (
            <span>
              Changed {formatShortDate(stored.changed_at.slice(0, 10))}
              {stored.changed_by_name ? ` by ${stored.changed_by_name}` : ''}
            </span>
          ) : null}
          {stored.notes ? <span className="whitespace-pre-wrap">{stored.notes}</span> : null}
          {error ? (
            <span role="alert" className="text-danger">
              {error}
            </span>
          ) : null}
        </div>
      ) : null}
    </>
  )
}
