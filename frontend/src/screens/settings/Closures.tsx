import { useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'

import { api, type Schemas } from '../../api/client'
import { $api } from '../../api/query'
import { isAdmin, useMe } from '../../app/session'
import { ActionDialog, unwrap, type ActionSpec } from '../../components/ActionDialog'
import { Dialog } from '../../components/Dialog'
import { CheckRow, DialogField, RaisedInput, RaisedSelect } from '../../components/Fields'
import { CardAction, CardHead, ListCard, ListHead, ListRow } from '../../components/ListTable'
import { LoadFailed, Loading } from '../../components/States'
import { Button, Callout } from '../../components/ui'
import { formatDateRange } from '../../lib/dates'

type Closure = Schemas['ClosureOut']
type Added = Schemas['ClosuresAddedOut']
type Agency = Schemas['AgencyOut']

const COLUMNS = '1.3fr 1.2fr 1fr 1.2fr 50px'
const closureDates = (closure: Closure) => formatDateRange(closure.starts_on, closure.ends_on)

function voidClosure(closure: Closure): ActionSpec {
  return {
    key: `void-${closure.id}`,
    title: 'Void closure days',
    subtitle: `${closure.agency_name} · ${closureDates(closure)}`,
    compact: true,
    submit: 'Void',
    danger: true,
    fields: [{ name: 'note', label: 'Why', required: true, marked: true }],
    run: async (values) => {
      unwrap(
        await api.POST('/api/records/agencies/closures/{closure_id}/void', {
          params: { path: { closure_id: closure.id } },
          body: { note: String(values.note ?? '').trim() },
        }),
      )
    },
  }
}

/**
 * Settings → Rules & calendars → Closure days (P16): days an office is closed, which count as
 * holidays when a clock is computed. Adding one does not move existing deadlines; it flags them
 * for a person. Add a closure day is 214.
 */
export function Closures() {
  const me = useMe()
  const client = useQueryClient()
  const [showPast, setShowPast] = useState(false)
  const [spec, setSpec] = useState<ActionSpec | null>(null)
  const [adding, setAdding] = useState(false)
  const [added, setAdded] = useState<Added | null>(null)
  const closures = $api.useQuery('get', '/api/records/agencies/closures', {
    params: { query: { include_past: showPast } },
  })
  const admin = isAdmin(me)
  const refresh = async () => {
    setSpec(null)
    await client.invalidateQueries({ queryKey: ['get', '/api/records/agencies/closures'] })
    await client.invalidateQueries({ queryKey: ['get', '/api/records/agencies/{agency_id}'] })
  }

  return (
    <ListCard aria-label="Closure days">
      <CardHead
        title="Closure days"
        action={
          <>
            <CardAction onClick={() => setShowPast((was) => !was)}>
              {showPast ? 'Coming up only' : 'Show past'}
            </CardAction>
            {admin ? (
              <CardAction onClick={() => setAdding(true)}>＋ Add a closure day</CardAction>
            ) : null}
          </>
        }
      />
      <p className="px-5 pb-3 text-125 leading-[1.6] text-ink-2">
        Days an office is closed count as holidays when a clock is computed. Adding one does not
        move existing deadlines; it flags them for a person.
      </p>
      {added ? (
        <div className="px-5 pb-3">
          <Callout role="status">
            Added for {added.closures.length} {added.closures.length === 1 ? 'agency' : 'agencies'}.{' '}
            {added.flagged_deadlines} deadlines flagged for review, {added.tasks_raised} tasks
            raised.
          </Callout>
        </div>
      ) : null}
      {closures.isPending ? (
        <Loading what="Loading closure days" />
      ) : closures.isError ? (
        <LoadFailed
          what="the closure days"
          error={closures.error}
          onRetry={() => void closures.refetch()}
        />
      ) : closures.data.length === 0 ? (
        <p className="border-t border-hairline px-5 py-3 text-125 text-muted">
          {showPast ? 'None recorded.' : 'None coming up.'}
        </p>
      ) : (
        <>
          <ListHead columns={COLUMNS} ruled="both">
            <span>Agency</span>
            <span>Dates</span>
            <span>Reason</span>
            <span>Citation</span>
            <span />
          </ListHead>
          {closures.data.map((closure, index) => (
            <ListRow key={closure.id} columns={COLUMNS} index={index} zebra={false}>
              <span className="min-w-0 truncate">{closure.agency_name}</span>
              <span className="min-w-0 truncate">{closureDates(closure)}</span>
              <span className="min-w-0 truncate">{closure.reason}</span>
              <span className="min-w-0 truncate" title={closure.citation}>
                {closure.citation}
              </span>
              <span className="justify-self-end">
                {admin ? (
                  <button
                    type="button"
                    onClick={() => setSpec(voidClosure(closure))}
                    className="font-medium text-brand hover:text-brand-dark"
                  >
                    Void
                  </button>
                ) : null}
              </span>
            </ListRow>
          ))}
        </>
      )}
      <ActionDialog spec={spec} onClose={() => setSpec(null)} onDone={() => void refresh()} />
      {adding ? (
        <AddClosure
          onClose={() => setAdding(false)}
          onDone={(result) => {
            setAdding(false)
            setAdded(result)
            void refresh()
          }}
        />
      ) : null}
    </ListCard>
  )
}

const CHOOSE = '__choose__'

/**
 * Add a closure day (214): Date · Until, Applies to, Reason, Citation. "Applies to" is every
 * agency of a jurisdiction, or agencies picked one by one (a list then opens under it). Until
 * follows Date until it is set by hand.
 */
function AddClosure({ onClose, onDone }: { onClose: () => void; onDone: (r: Added) => void }) {
  const agencies = $api.useQuery('get', '/api/records/agencies')
  const all = (agencies.data ?? []).slice().sort((a, b) => a.name.localeCompare(b.name))
  const jurisdictions = [...new Set(all.map((a) => a.jurisdiction))].sort()
  const [appliesTo, setAppliesTo] = useState('')
  const [chosen, setChosen] = useState<string[]>([])
  const [from, setFrom] = useState('')
  const [until, setUntil] = useState('')
  const [reason, setReason] = useState('')
  const [citation, setCitation] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const formId = 'add-closure'
  const scope = appliesTo
  const ids =
    scope === CHOOSE
      ? chosen
      : scope
        ? all.filter((a) => a.jurisdiction === scope).map((a) => a.id)
        : []

  const toggle = (agency: Agency, on: boolean) =>
    setChosen((current) =>
      on ? [...new Set([...current, agency.id])] : current.filter((id) => id !== agency.id),
    )
  const changeFrom = (next: string) => {
    setUntil((current) => (!current || current === from || current < next ? next : current))
    setFrom(next)
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!scope) return setError('Choose who it applies to.')
    if (ids.length === 0) return setError('Choose at least one agency.')
    if (until && until < from) return setError('Until cannot be before the date.')
    if (!citation.trim()) return setError('The citation is required.')
    setBusy(true)
    setError(null)
    try {
      onDone(
        unwrap(
          await api.POST('/api/records/agencies/closures', {
            body: {
              agency_ids: ids,
              starts_on: from,
              ends_on: until || from,
              reason: reason.trim(),
              citation: citation.trim(),
            },
          }),
        ),
      )
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Something went wrong.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      title="Add a closure day"
      open
      onClose={onClose}
      width={480}
      top={70}
      footer={
        <>
          <Button kind="text" pill onClick={onClose}>
            Cancel
          </Button>
          <Button kind="primary" pill type="submit" form={formId} busy={busy}>
            Add
          </Button>
        </>
      }
    >
      <form id={formId} className="flex flex-col gap-3.5" onSubmit={(event) => void submit(event)}>
        <div className="flex gap-3">
          <div className="min-w-0 flex-1">
            <DialogField label="Date">
              <RaisedInput
                compact
                type="date"
                required
                autoFocus
                data-autofocus
                value={from}
                onChange={(event) => changeFrom(event.target.value)}
              />
            </DialogField>
          </div>
          <div className="min-w-0 flex-1">
            <DialogField label="Until">
              <RaisedInput
                compact
                type="date"
                min={from || undefined}
                value={until}
                onChange={(event) => setUntil(event.target.value)}
              />
            </DialogField>
          </div>
        </div>
        <DialogField label="Applies to">
          <RaisedSelect
            compact
            required
            value={scope}
            onChange={(event) => setAppliesTo(event.target.value)}
          >
            <option value="">Choose…</option>
            {jurisdictions.map((value) => (
              <option key={value} value={value}>
                Every agency in {value}
              </option>
            ))}
            <option value={CHOOSE}>Agencies I choose…</option>
          </RaisedSelect>
        </DialogField>
        {scope === CHOOSE ? (
          agencies.isPending ? (
            <Loading what="Loading agencies" />
          ) : agencies.isError ? (
            <LoadFailed
              what="the agencies"
              error={agencies.error}
              onRetry={() => void agencies.refetch()}
            />
          ) : (
            <div className="flex max-h-48 flex-col gap-1.5 overflow-y-auto rounded-12 bg-card-white px-3 py-2.5 shadow-field">
              {all.length === 0 ? (
                <p className="text-12 text-muted">There is nothing to choose from.</p>
              ) : null}
              {all.map((agency) => (
                <CheckRow
                  key={agency.id}
                  checked={chosen.includes(agency.id)}
                  onChange={(checked) => toggle(agency, checked)}
                >
                  {agency.name}
                  <span className="ml-1.5 text-12 text-muted">{agency.jurisdiction}</span>
                </CheckRow>
              ))}
            </div>
          )
        ) : null}
        <DialogField label="Reason">
          <RaisedInput
            compact
            required
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
        </DialogField>
        <DialogField label="Citation" required>
          <RaisedInput
            compact
            required
            value={citation}
            onChange={(event) => setCitation(event.target.value)}
          />
        </DialogField>
        {error ? (
          <p
            role="alert"
            className="rounded-10 bg-danger-tint px-3.5 py-[11px] text-125 text-danger-2"
          >
            {error}
          </p>
        ) : null}
      </form>
    </Dialog>
  )
}
