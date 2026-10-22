import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import type { Schemas } from '../../api/client'
import { $api, messageOf } from '../../api/query'
import { useMe } from '../../app/session'
import { Dialog } from '../../components/Dialog'
import { PillChoice, RaisedTextarea } from '../../components/Fields'
import { LoadFailed, Loading } from '../../components/States'
import { Button } from '../../components/ui'
import { formatInstant } from '../../lib/dates'
import { Row, RowAction, SettingsCard } from './rows'

type Version = Schemas['AgreementVersionOut']
type Acceptance = Schemas['AcceptanceOut']

/** A signed copy's address: the person's own, or anyone's for an administrator. */
export function acceptancePdf(id: string): string {
  return `/api/core/agreements/acceptances/${id}/pdf`
}

const LINK =
  'w-11 shrink-0 text-right text-125 font-medium whitespace-nowrap text-brand hover:text-brand-dark'

/**
 * Settings → Organisation → Agreements (P20i; no screen draws it): the agreements the new-user
 * setup asks people to sign (010 confidentiality, 011 working as an intern), for an
 * administrator. Each row says the version in force, when it was published and how many have
 * signed it, and whether it is still the design's placeholder text; "N signed" lists them with
 * their signed copies, and Edit publishes the next version, which everyone it applies to is asked
 * to agree to at their next sign-in. A version is never edited in place.
 */
export function AgreementsCard() {
  const versions = $api.useQuery('get', '/api/core/agreements')
  const [editing, setEditing] = useState<Version | null>(null)
  const [viewing, setViewing] = useState<string | null>(null)
  const me = useMe()
  const current = (versions.data ?? []).filter((v) => v.is_current)
  return (
    <SettingsCard title="Agreements">
      {versions.isPending ? (
        <Loading what="Reading the agreements" />
      ) : versions.isError ? (
        <LoadFailed
          what="the agreements"
          error={versions.error}
          onRetry={() => void versions.refetch()}
        />
      ) : current.length === 0 ? (
        <Row label="No agreements" hint="None has been published." action={null} />
      ) : (
        current.map((version) => (
          <Row
            key={version.kind}
            label={version.title}
            hint={
              `Version ${version.version} · published ${formatInstant(version.published_at, me.timezone)}` +
              (version.published_by_name ? ` by ${version.published_by_name}` : '') +
              (version.is_placeholder ? ' · the design’s placeholder text' : '')
            }
            value={
              <button
                type="button"
                onClick={() => setViewing(version.kind)}
                className="text-13 text-brand hover:text-brand-dark"
              >
                {version.signed_count} signed
              </button>
            }
            action={<RowAction onClick={() => setEditing(version)}>Edit</RowAction>}
          />
        ))
      )}
      {editing ? <PublishDialog current={editing} onClose={() => setEditing(null)} /> : null}
      {viewing ? (
        <SignedDialog
          versions={(versions.data ?? []).filter((v) => v.kind === viewing)}
          onClose={() => setViewing(null)}
        />
      ) : null}
    </SettingsCard>
  )
}

/** Edit an agreement: its text as published, and Publish, which makes the next version. */
function PublishDialog({ current, onClose }: { current: Version; onClose: () => void }) {
  const client = useQueryClient()
  const [body, setBody] = useState(current.body)
  const publish = $api.useMutation('post', '/api/core/agreements', {
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ['get', '/api/core/agreements'] })
      onClose()
    },
  })
  const changed = body.trim() !== current.body.trim() && body.trim() !== ''
  return (
    <Dialog
      title={current.title}
      subtitle={`Version ${current.version} is in force. Publishing makes version ${current.version + 1}, and everyone it applies to is asked to agree to it at their next sign-in.`}
      open
      onClose={onClose}
      width={620}
      top={70}
      footer={
        <>
          <Button kind="text" pill onClick={onClose}>
            Cancel
          </Button>
          <Button
            kind="primary"
            pill
            busy={publish.isPending}
            disabled={!changed}
            onClick={() => publish.mutate({ body: { kind: current.kind, body } })}
          >
            Publish version {current.version + 1}
          </Button>
        </>
      }
    >
      <RaisedTextarea
        aria-label={`${current.title}, the text`}
        minHeight={300}
        className="max-h-[52vh] overflow-y-auto"
        value={body}
        onChange={(event) => setBody(event.target.value)}
      />
      <p className="text-12 leading-[1.5] text-muted">
        One clause to a paragraph, with a blank line between them. A clause’s lead between ** and **
        is set in bold.
      </p>
      {publish.isError ? (
        <p
          role="alert"
          className="rounded-10 bg-danger-tint px-3.5 py-[11px] text-125 text-danger-2"
        >
          {messageOf(publish.error)}
        </p>
      ) : null}
    </Dialog>
  )
}

/** Who signed a version of an agreement, newest first, each with their signed copy. */
function SignedDialog({ versions, onClose }: { versions: Version[]; onClose: () => void }) {
  const me = useMe()
  const [chosen, setChosen] = useState(versions[0]?.id ?? '')
  const version = versions.find((v) => v.id === chosen)
  const rows = $api.useQuery(
    'get',
    '/api/core/agreements/acceptances',
    { params: { query: { agreement_id: chosen } } },
    { enabled: Boolean(chosen) },
  )
  return (
    <Dialog
      title={version ? `${version.title}: signed` : 'Signed'}
      open
      onClose={onClose}
      width={520}
      footer={
        <Button kind="text" pill onClick={onClose}>
          Close
        </Button>
      }
    >
      {versions.length > 1 ? (
        <PillChoice
          label="Version"
          size="md"
          options={versions.map((v) => ({ value: v.id, label: `Version ${v.version}` }))}
          value={chosen}
          onChange={setChosen}
        />
      ) : null}
      {rows.isPending ? (
        <Loading what="Reading who signed" />
      ) : rows.isError ? (
        <LoadFailed what="who signed" error={rows.error} onRetry={() => void rows.refetch()} />
      ) : rows.data.length === 0 ? (
        <p className="text-13 text-muted">Nobody has signed this version yet.</p>
      ) : (
        <ul className="-mx-[22px] max-h-[50vh] overflow-y-auto">
          {rows.data.map((row: Acceptance) => (
            <li
              key={row.id}
              className="flex items-center gap-3 border-t border-line-row px-[22px] py-2.5"
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate text-13 text-ink">{row.user_name}</span>
                <span className="block text-12 text-muted">
                  {formatInstant(row.accepted_at, me.timezone)}
                </span>
              </span>
              <a href={acceptancePdf(row.id)} download className={LINK}>
                PDF
              </a>
            </li>
          ))}
        </ul>
      )}
    </Dialog>
  )
}

/**
 * Settings → Profile → Agreements (P20i): what the person signed in the new-user setup, each
 * with the PDF of exactly what they signed.
 */
export function MyAgreementsCard() {
  const me = useMe()
  const mine = $api.useQuery('get', '/api/core/agreements/acceptances')
  if (mine.isPending || mine.isError || mine.data.length === 0) return null
  return (
    <SettingsCard title="Agreements">
      {mine.data.map((row) => (
        <Row
          key={row.id}
          label={row.title}
          hint={`Version ${row.version} · agreed ${formatInstant(row.accepted_at, me.timezone)}`}
          action={
            <a href={acceptancePdf(row.id)} download className={LINK}>
              PDF
            </a>
          }
        />
      ))}
    </SettingsCard>
  )
}
