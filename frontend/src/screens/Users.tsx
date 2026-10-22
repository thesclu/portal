import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router'

import { api, type Schemas } from '../api/client'
import { $api } from '../api/query'
import { PageHeader } from '../app/layout'
import { useMe } from '../app/session'
import { ActionDialog, unwrap, type ActionSpec } from '../components/ActionDialog'
import { DotsIcon } from '../components/icons'
import { ListCard, ListHead, ListRow } from '../components/ListTable'
import { MenuPanel, type MenuItem } from '../components/Menu'
import { LoadFailed } from '../components/States'
import { Avatar, Callout, cx } from '../components/ui'
import { civilDayOf, formatInstant, formatLastActive, formatShortDate } from '../lib/dates'
import { initialsOf } from '../lib/presence'
import { roleShort } from '../lib/roles'
import { ChangeRoleDialog, InviteDialog } from './users/RoleDialogs'

type User = Schemas['UserOut']

const COLUMNS = '1.3fr 1.5fr 110px 150px 40px'

const invitePending = (user: User) => user.invite_pending_until ?? null

/**
 * Users (205–208), administrators: everyone with an account, active first, with their role and
 * when they were last about. A row's ⋯ opens Change role (207), Send password reset, View
 * activity (190) and Deactivate (208); a deactivated person can be reactivated, and an invite
 * that was not taken up sent again. Invite user is 206. Nobody is ever deleted.
 *
 * P25a: sign-in is Keycloak's, and Users drives it. Send password reset and the invitation are
 * Keycloak's emails; Sign out everywhere and Reset two-step (for someone who lost every passkey
 * and recovery code) are new in the menu (not drawn). Each is a change owed to Keycloak, applied
 * in the background and retried: while one is on its way the row's last column says "Updating
 * sign-in", and if Keycloak refused it, "Sign-in update failed" with the reason and Try again.
 * "Add with a password" is gone: the portal holds no passwords.
 */
export function Users() {
  const me = useMe()
  const navigate = useNavigate()
  const client = useQueryClient()
  const users = $api.useQuery('get', '/api/core/users')
  const [spec, setSpec] = useState<ActionSpec | null>(null)
  const [inviting, setInviting] = useState(false)
  const [changing, setChanging] = useState<User | null>(null)
  const [menu, setMenu] = useState<string | null>(null)
  const [said, setSaid] = useState<string | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const refresh = () => client.invalidateQueries({ queryKey: ['get', '/api/core/users'] })
  const all = users.data ?? []
  const rows = [...all.filter((u) => u.is_active), ...all.filter((u) => !u.is_active)]
  const active = all.filter((u) => u.is_active).length
  const inactive = all.length - active
  const others = (user: User) =>
    all.filter((u) => u.is_active && u.records_role !== 'requester' && u.id !== user.id)

  async function quietly(run: () => Promise<unknown>, done: string) {
    setSaid(null)
    setFailure(null)
    try {
      await run()
      setSaid(done)
      await refresh()
    } catch (caught) {
      setFailure(caught instanceof Error ? caught.message : 'The server refused this.')
    }
  }

  const itemsFor = (user: User): MenuItem[] => {
    const pending = invitePending(user)
    const signsIn = user.sign_in.state !== 'none'
    const path = { params: { path: { user_id: user.id } } }
    const items: MenuItem[] = [{ label: 'Change role', onPick: () => setChanging(user) }]
    if (user.sign_in.state === 'failed') {
      items.push({
        label: 'Try the sign-in update again',
        onPick: () =>
          void quietly(
            async () => unwrap(await api.POST('/api/core/users/{user_id}/sign-in/retry', path)),
            `The sign-in update for ${user.name} is on its way again.`,
          ),
      })
    }
    if (user.is_active && !pending && signsIn) {
      items.push({
        label: 'Send password reset',
        onPick: () =>
          void quietly(
            async () => unwrap(await api.POST('/api/core/users/{user_id}/password-reset', path)),
            `A password reset link is on its way to ${user.email}.`,
          ),
      })
      if (user.id !== me.id) {
        items.push({
          label: 'Sign out everywhere',
          onPick: () =>
            void quietly(
              async () =>
                unwrap(await api.POST('/api/core/users/{user_id}/sign-out-everywhere', path)),
              `${user.name} is signed out everywhere.`,
            ),
        })
        items.push({ label: 'Reset two-step', onPick: () => setSpec(resetTwoStep(user)) })
      }
    }
    if (
      signsIn &&
      (pending || user.sign_in.state === 'unlinked' || (!user.is_active && !user.last_login))
    ) {
      items.push({
        label: 'Resend invite',
        onPick: () =>
          void quietly(
            async () =>
              unwrap(
                await api.POST('/api/core/users/{user_id}/resend-invite', {
                  params: { path: { user_id: user.id } },
                }),
              ),
            `A new invite was sent to ${user.email}.`,
          ),
      })
    }
    items.push({ label: 'View activity', onPick: () => void navigate(`/activity?who=${user.id}`) })
    if (user.is_active && user.id !== me.id) {
      items.push({
        label: 'Deactivate',
        danger: true,
        separator: true,
        onPick: () => void openDeactivate(user),
      })
    } else if (!user.is_active && !pending) {
      items.push({
        label: 'Reactivate',
        separator: true,
        onPick: () =>
          void quietly(
            async () =>
              unwrap(
                await api.PATCH('/api/core/users/{user_id}', {
                  params: { path: { user_id: user.id } },
                  body: { is_active: true },
                }),
              ),
            `${user.name} can sign in again.`,
          ),
      })
    }
    return items
  }

  async function openDeactivate(user: User) {
    // How many open tasks they hold, for "Give their N open tasks to" (208).
    const tasks = await api.GET('/api/records/tasks', {
      params: { query: { assignee: user.id, state: 'open' } },
    })
    setSpec(deactivate(user, others(user), tasks.data?.length ?? null, me.id))
  }

  return (
    <div className="flex flex-col leading-[normal]">
      <PageHeader title="Users" />
      <div className="flex min-w-0 flex-col gap-3.5 px-2 pb-2">
        <div className="flex items-center gap-2">
          <span className="text-125 text-muted">
            {users.data ? `${active} active · ${inactive} deactivated` : ''}
          </span>
          <span className="ml-auto flex items-center gap-2">
            <button
              type="button"
              onClick={() => setInviting(true)}
              className="inline-flex h-8 items-center rounded-pill bg-brand px-3.5 text-125 font-medium whitespace-nowrap text-white hover:bg-brand-dark"
            >
              Invite user
            </button>
          </span>
        </div>
        {said ? <Callout role="status">{said}</Callout> : null}
        {failure ? (
          <Callout tone="danger" role="alert">
            {failure}
          </Callout>
        ) : null}
        <ListCard aria-label="Users" className="overflow-visible">
          <ListHead columns={COLUMNS} className="rounded-t-12">
            <span>Name</span>
            <span>Email</span>
            <span>Role</span>
            <span>Last active</span>
            <span />
          </ListHead>
          {users.isPending ? (
            Array.from({ length: 4 }, (_, i) => (
              <div
                key={i}
                aria-hidden
                style={{ gridTemplateColumns: COLUMNS, opacity: 1 - i * 0.18 }}
                className="grid items-center gap-x-3.5 px-9 py-[18px]"
              >
                {['70%', '80%', '50%', '60%'].map((width, j) => (
                  <span
                    key={j}
                    className="block h-[11px] rounded-4 bg-fill-skeleton"
                    style={{ width }}
                  />
                ))}
              </div>
            ))
          ) : users.isError ? (
            <LoadFailed
              what="the people"
              error={users.error}
              onRetry={() => void users.refetch()}
            />
          ) : (
            rows.map((user, index) => (
              <UserRow
                key={user.id}
                user={user}
                index={index}
                open={menu === user.id}
                onToggle={() => setMenu((was) => (was === user.id ? null : user.id))}
                onClose={() => setMenu(null)}
                items={itemsFor(user)}
              />
            ))
          )}
        </ListCard>
      </div>
      <ActionDialog
        spec={spec}
        onClose={() => setSpec(null)}
        onDone={() => {
          setSpec(null)
          void refresh()
        }}
      />
      <InviteDialog
        open={inviting}
        onClose={() => setInviting(false)}
        onDone={(email, signsIn) => {
          setSaid(
            signsIn
              ? `An invite is on its way to ${email}.`
              : `Added ${email}. Requesters don’t sign in to the portal, so no invite was sent.`,
          )
          void refresh()
        }}
      />
      <ChangeRoleDialog
        user={changing}
        onClose={() => setChanging(null)}
        onDone={() => {
          setChanging(null)
          void refresh()
        }}
      />
    </div>
  )
}

function UserRow({
  user,
  index,
  open,
  onToggle,
  onClose,
  items,
}: {
  user: User
  index: number
  open: boolean
  onToggle: () => void
  onClose: () => void
  items: MenuItem[]
}) {
  const me = useMe()
  const ref = useRef<HTMLDivElement>(null)
  const pending = invitePending(user)

  useEffect(() => {
    if (!open) return
    function away(event: PointerEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) onClose()
    }
    function key(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('pointerdown', away)
    document.addEventListener('keydown', key)
    return () => {
      document.removeEventListener('pointerdown', away)
      document.removeEventListener('keydown', key)
    }
  }, [open, onClose])

  const quiet = !user.is_active && !pending
  return (
    <div ref={ref} className="relative">
      <ListRow columns={COLUMNS} index={index} className={open ? 'bg-row-alt' : undefined}>
        <span
          className={cx('flex min-w-0 items-center gap-2.5', quiet ? 'text-muted' : 'text-ink')}
        >
          <Avatar
            initials={initialsOf(user.name)}
            size={26}
            className={quiet ? 'bg-muted-2' : undefined}
          />
          <span className="truncate">{user.name}</span>
        </span>
        <span className="min-w-0 truncate text-ink-2">{user.email}</span>
        <span className={quiet ? 'text-muted' : 'text-ink'}>
          {user.records_role ? roleShort(user.records_role) || 'No role' : 'No role'}
        </span>
        {user.sign_in.state === 'failed' ? (
          <span className="min-w-0 truncate text-warn" title={user.sign_in.detail}>
            Sign-in update failed
          </span>
        ) : user.sign_in.state === 'pending' ? (
          <span className="min-w-0 truncate text-muted" title={pendingWhat(user.sign_in.action)}>
            Updating sign-in…
          </span>
        ) : pending ? (
          <span
            className="min-w-0 truncate text-muted"
            title={`The link works until ${formatInstant(pending, me.timezone)}`}
          >
            Invited
          </span>
        ) : user.sign_in.state === 'unlinked' && user.is_active ? (
          <span
            className="min-w-0 truncate text-muted"
            title="An account from before Keycloak: Resend invite gives them a sign-in"
          >
            No sign-in yet
          </span>
        ) : quiet ? (
          <span className="text-warn">
            {/* P23b (205): the date, when it was kept. */}
            {user.deactivated_at
              ? `Deactivated ${formatShortDate(civilDayOf(user.deactivated_at, me.timezone))}`
              : 'Deactivated'}
          </span>
        ) : (
          <span className="text-muted">
            {formatLastActive(user.last_active_at ?? user.last_login, me.timezone)}
          </span>
        )}
        <button
          type="button"
          aria-label={`More on ${user.name}`}
          aria-haspopup="menu"
          aria-expanded={open}
          onClick={onToggle}
          className={cx(
            'inline-flex size-7 items-center justify-center justify-self-end rounded-chip hover:bg-hover',
            open && 'bg-hover',
          )}
        >
          <DotsIcon size={15} fill="var(--color-ink-3)" stroke="none" />
        </button>
      </ListRow>
      {open ? (
        <MenuPanel
          aria-label={`More on ${user.name}`}
          items={items}
          width={200}
          onPicked={onClose}
          className="absolute top-10 right-2.5 z-20"
        />
      ) : null}
    </div>
  )
}

// ---- The dialogs (206–208) ----------------------------------------------------------------------

const text = (value: unknown) => String(value ?? '').trim()

/** What a pending sign-in change is, for the row's tooltip. */
function pendingWhat(action: string): string {
  const words: Record<string, string> = {
    sync: 'Their Keycloak account is being brought in line',
    invite: 'The invitation is on its way',
    password_reset: 'The password reset email is on its way',
    end_sessions: 'Their sessions are being ended',
    reset_second_step: 'Their two-step sign-in is being reset',
  }
  return words[action] ?? 'A change is on its way to Keycloak'
}

/** Reset two-step (P25a; not drawn): for someone who lost every passkey and recovery code, once
 *  it is surely them. Their passkeys and codes go and their sessions end; at their next sign-in
 *  they add a new passkey after their password. */
function resetTwoStep(user: User): ActionSpec {
  return {
    key: `reset-two-step-${user.id}`,
    title: `Reset ${user.name}’s two-step sign-in?`,
    width: 440,
    submit: 'Reset two-step',
    danger: true,
    fields: [],
    intro:
      'Their passkeys and recovery codes stop working and they are signed out everywhere. At their next sign-in they add a new passkey after their password. Do this only once you are sure it is them.',
    run: async () => {
      unwrap(
        await api.POST('/api/core/users/{user_id}/reset-second-step', {
          params: { path: { user_id: user.id } },
        }),
      )
    },
  }
}

/** Deactivate (208): they can no longer sign in; nothing they did is removed, and they can be
 *  reactivated. Their open tasks go to the person chosen, by default the administrator doing it
 *  (42f; a task on a request the chosen person may not open is left unassigned instead). */
function deactivate(user: User, others: User[], tasks: number | null, meId: string): ActionSpec {
  const field =
    tasks === 0
      ? 'They have no open tasks'
      : `Give their ${tasks === null ? '' : `${tasks} `}open ${tasks === 1 ? 'task' : 'tasks'} to`
  return {
    key: `deactivate-${user.id}`,
    title: `Deactivate ${user.name}?`,
    width: 440,
    submit: 'Deactivate',
    danger: true,
    fields:
      tasks === 0
        ? []
        : [
            {
              name: 'hand_to',
              label: field,
              type: 'select',
              initial: others.some((o) => o.id === meId) ? meId : '',
              options: [
                ...others.map((other) => ({ value: other.id, label: other.name })),
                { value: '', label: 'Nobody: leave them unassigned' },
              ],
            },
          ],
    intro: tasks === 0 ? 'They have no open tasks.' : undefined,
    run: async (values) => {
      const handTo = text(values.hand_to)
      unwrap(
        await api.PATCH('/api/core/users/{user_id}', {
          params: { path: { user_id: user.id } },
          // "Nobody" is said out loud: left out, the server hands them to the admin doing it.
          body: { is_active: false, reassign_tasks_to: handTo || null },
        }),
      )
    },
  }
}
