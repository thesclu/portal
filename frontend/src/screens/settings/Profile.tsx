import { useQueryClient } from '@tanstack/react-query'
import { useMemo, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router'

import { $api, messageOf } from '../../api/query'
import { useMe } from '../../app/session'
import { RaisedInput, RaisedSelect } from '../../components/Fields'
import { roleLabel } from '../../lib/roles'
import { timeZones, zoneLabel } from '../../lib/settings'
import { MyAgreementsCard } from './Agreements'
import { Row, RowAction, SettingsCard } from './rows'
import { Security } from './Security'
import { SignatureThumb, useSignature } from './Signature'

/** "Cancel" beside a row's Save. */
function Cancel({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="text-125 font-medium whitespace-nowrap text-ink-2 hover:text-ink"
    >
      Cancel
    </button>
  )
}

function useUpdateMe() {
  const client = useQueryClient()
  return $api.useMutation('patch', '/api/core/auth/me', {
    onSuccess: (updated) => client.setQueryData(['get', '/api/core/auth/me', {}], updated),
  })
}

/**
 * Settings → Profile (200): the person's name, email, role and time zone, then Security (the
 * password and two-step sign-in, 221 and 222), the door to their signature (217, where the title
 * they sign with is edited since P20i) and the agreements they signed in the new-user setup, each
 * with its PDF (P20i). The email is the address they sign in with and only an administrator
 * could change it, so it has no Edit; the role is an administrator's to change under Users.
 */
export function Profile() {
  const me = useMe()
  const navigate = useNavigate()
  const update = useUpdateMe()
  const [editing, setEditing] = useState<'name' | 'zone' | null>(null)
  const [name, setName] = useState(me.name)
  const zones = useMemo(() => timeZones(me.timezone), [me.timezone])
  const signature = useSignature(me.id)
  const error = update.isError ? messageOf(update.error) : null

  function saveName(event: FormEvent) {
    event.preventDefault()
    update.mutate({ body: { name: name.trim() } }, { onSuccess: () => setEditing(null) })
  }

  return (
    <>
      <SettingsCard title="Profile">
        {editing === 'name' ? (
          <form onSubmit={saveName}>
            <Row
              label="Name"
              error={error}
              action={
                <RowAction type="submit" disabled={update.isPending || !name.trim()}>
                  Save
                </RowAction>
              }
            >
              <RaisedInput
                autoFocus
                required
                aria-label="Name"
                className="h-8! w-64!"
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
              <Cancel onClick={() => setEditing(null)} />
            </Row>
          </form>
        ) : (
          <Row
            label="Name"
            value={me.name}
            action={
              <RowAction
                onClick={() => {
                  update.reset()
                  setName(me.name)
                  setEditing('name')
                }}
              >
                Edit
              </RowAction>
            }
          />
        )}
        <Row label="Email" value={me.email} action={null} />
        <Row label="Role" value={roleLabel(me.records_role)} action={null} />
        {editing === 'zone' ? (
          <Row label="Time zone" error={error} action={null}>
            <span className="w-64">
              <RaisedSelect
                compact
                autoFocus
                aria-label="Time zone"
                className="h-8!"
                disabled={update.isPending}
                value={me.timezone}
                onChange={(event) =>
                  update.mutate(
                    { body: { timezone: event.target.value } },
                    { onSuccess: () => setEditing(null) },
                  )
                }
              >
                {zones.map((zone) => (
                  <option key={zone} value={zone}>
                    {zone.replaceAll('_', ' ')}
                  </option>
                ))}
              </RaisedSelect>
            </span>
            <Cancel onClick={() => setEditing(null)} />
          </Row>
        ) : (
          <Row
            label="Time zone"
            value={zoneLabel(me.timezone)}
            action={
              <RowAction
                onClick={() => {
                  update.reset()
                  setEditing('zone')
                }}
              >
                Edit
              </RowAction>
            }
          />
        )}
      </SettingsCard>
      <Security />
      {/* Everyone has one since the new-user setup asks for it (P20i), requesters included. */}
      <SettingsCard title="Your signature">
        <Row
          label="Signature"
          value={
            signature.data?.has_image ? (
              <SignatureThumb userId={me.id} version={signature.data.updated_at} />
            ) : signature.isPending ? undefined : (
              <span className="text-muted">None yet</span>
            )
          }
          action={<RowAction onClick={() => void navigate('/settings/signature')}>Edit</RowAction>}
        />
      </SettingsCard>
      <MyAgreementsCard />
    </>
  )
}
