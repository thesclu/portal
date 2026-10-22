import { useState } from 'react'

import { api, type Schemas } from '../../api/client'
import { messageOf } from '../../api/query'
import { Dialog } from '../../components/Dialog'
import { DialogField, RaisedInput } from '../../components/Fields'
import { cx } from '../../components/ui'
import { ROLES, ROLE_DESCRIPTION, ROLE_LABEL, type Role } from '../../lib/roles'

type User = Schemas['UserOut']

/**
 * The six roles as 206 and 207 draw them: one row each, 10px 12px, 12 px round, 2 px apart, the
 * name 13/600 over its one line 12/1.45; the one chosen on the brand tint with the 5 px ring.
 */
export function RoleList({
  value,
  onChange,
  label = 'Role',
}: {
  value: Role
  onChange: (role: Role) => void
  label?: string
}) {
  return (
    <div role="radiogroup" aria-label={label} className="flex flex-col gap-0.5">
      {ROLES.map((role) => {
        const on = role === value
        return (
          <label
            key={role}
            className={cx(
              'flex cursor-pointer items-start gap-3 rounded-12 px-3 py-2.5 leading-[normal]',
              on ? 'bg-brand-tint' : 'hover:bg-row-alt',
            )}
          >
            <input
              type="radio"
              className="sr-only"
              name="role"
              checked={on}
              onChange={() => onChange(role)}
            />
            <span
              aria-hidden
              className={cx(
                'mt-0.5 box-border size-4 shrink-0 rounded-pill',
                on ? 'border-[5px] border-brand' : 'border-[1.5px] border-line-control',
              )}
            />
            <span className="min-w-0">
              <span className="block text-13 font-semibold text-ink">{ROLE_LABEL[role]}</span>
              <span className="mt-0.5 block text-12 leading-[1.45] text-ink-2">
                {ROLE_DESCRIPTION[role]}
              </span>
            </span>
          </label>
        )
      })}
    </div>
  )
}

function Footer({
  busy,
  submit,
  onCancel,
  disabled,
}: {
  busy: boolean
  submit: string
  onCancel: () => void
  disabled?: boolean
}) {
  return (
    <div className="flex items-center justify-end gap-2.5 px-6 pt-4 pb-5">
      <button
        type="button"
        onClick={onCancel}
        className="rounded-pill px-3 py-[9px] text-13 leading-[normal] font-medium text-ink-2 hover:bg-hover"
      >
        Cancel
      </button>
      <button
        type="submit"
        disabled={busy || disabled}
        className="rounded-pill bg-brand px-[18px] py-[9px] text-13 leading-[normal] font-semibold text-white hover:bg-brand-dark disabled:opacity-55"
      >
        {submit}
      </button>
    </div>
  )
}

/**
 * Invite user (206): name, email and the role, as the six drawn in 207 with their lines (the v6
 * export drew the list loose in the Users table behind the dialog and kept a Role box in it; the
 * list belongs in the dialog, CHANGES "Role is now a list of the six roles"). The account is made
 * now; the person sets their own password from the emailed link.
 */
export function InviteDialog({
  open,
  onClose,
  onDone,
}: {
  open: boolean
  onClose: () => void
  /** P25a: `signsIn` false for a requester, who gets the account and no invitation. */
  onDone: (email: string, signsIn: boolean) => void
}) {
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<Role>('staff')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const close = () => {
    setName('')
    setEmail('')
    setRole('staff')
    setError(null)
    onClose()
  }
  async function submit() {
    setBusy(true)
    setError(null)
    const result = await api.POST('/api/core/users/invite', {
      body: { name: name.trim(), email: email.trim(), records_role: role },
    })
    setBusy(false)
    if (result.error) {
      setError(messageOf(result.error))
      return
    }
    onDone(email.trim(), result.data.sign_in.state !== 'none')
    close()
  }
  return (
    <Dialog title="Invite user" open={open} onClose={close} width={440} bare>
      <form
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
      >
        <div className="px-[22px] pt-5">
          <h2 className="font-display text-18 leading-[normal] font-extrabold tracking-dialog text-ink">
            Invite user
          </h2>
        </div>
        <div className="flex flex-col gap-3.5 px-[22px] pt-[18px] pb-5">
          {error ? (
            <p
              role="alert"
              className="rounded-10 bg-danger-tint px-3.5 py-[11px] text-125 leading-[1.5] text-danger-2"
            >
              {error}
            </p>
          ) : null}
          <DialogField label="Name">
            <RaisedInput
              required
              value={name}
              onChange={(event) => setName(event.target.value)}
              autoComplete="off"
            />
          </DialogField>
          <DialogField label="Email">
            <RaisedInput
              required
              type="email"
              data-autofocus
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              autoComplete="off"
            />
          </DialogField>
          <div className="flex flex-col gap-1.5">
            <span className="text-125 leading-[normal] text-ink-2">Role</span>
            <RoleList value={role} onChange={setRole} />
          </div>
        </div>
        <Footer busy={busy} submit="Send invite" onCancel={close} />
      </form>
    </Dialog>
  )
}

/**
 * Change role (207): the person's name under the title, then the six roles, as drawn: no Director
 * or "files without review" mark (both retired, 42d). Making someone a requester takes them off
 * every access list, which the line under the list says before it is done.
 */
export function ChangeRoleDialog({
  user,
  onClose,
  onDone,
}: {
  user: User | null
  onClose: () => void
  onDone: () => void
}) {
  return user ? (
    <ChangeRoleOpen key={user.id} user={user} onClose={onClose} onDone={onDone} />
  ) : (
    <Dialog title="Change role" open={false} onClose={onClose}>
      {null}
    </Dialog>
  )
}

function ChangeRoleOpen({
  user,
  onClose,
  onDone,
}: {
  user: User
  onClose: () => void
  onDone: () => void
}) {
  const current = (user.records_role ?? 'staff') as Role
  const [role, setRole] = useState<Role>(current)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function submit() {
    setBusy(true)
    setError(null)
    const result = await api.PATCH('/api/core/users/{user_id}', {
      params: { path: { user_id: user.id } },
      body: { role },
    })
    setBusy(false)
    if (result.error) {
      setError(messageOf(result.error))
      return
    }
    onDone()
  }
  return (
    <Dialog title="Change role" open onClose={onClose} width={440} bare>
      <form
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
      >
        <div className="flex items-start gap-3 px-[22px] pt-5">
          <div className="min-w-0 flex-1">
            <h2 className="font-display text-18 leading-[normal] font-extrabold tracking-dialog text-ink">
              Change role
            </h2>
            <p className="mt-[3px] text-125 leading-[normal] text-muted">{user.name}</p>
          </div>
        </div>
        {error ? (
          <p
            role="alert"
            className="mx-[22px] mt-3 rounded-10 bg-danger-tint px-3.5 py-[11px] text-125 leading-[1.5] text-danger-2"
          >
            {error}
          </p>
        ) : null}
        <RoleList value={role} onChange={setRole} />
        {role === 'requester' && current !== 'requester' ? (
          <p className="px-[22px] pt-2 text-12 leading-[1.45] text-muted">
            A requester sees only their own requests; anything given to {user.name} stops being
            theirs.
          </p>
        ) : null}
        <Footer busy={busy} submit="Change role" onCancel={onClose} />
      </form>
    </Dialog>
  )
}
