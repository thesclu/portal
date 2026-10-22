import { useMemo, useState } from 'react'

import type { Schemas } from '../../api/client'
import { $api } from '../../api/query'
import { useMe } from '../../app/session'
import { Empty, LoadFailed, Loading } from '../../components/States'
import { Field, GroupLabel, Select, cx } from '../../components/ui'
import { formatInstant } from '../../lib/dates'
import { diffLines, revisionLines, type DiffLine } from './diff'
import { filings } from './shared'

type Revision = Schemas['RevisionOut']

const MARK: Record<DiffLine['kind'], string> = { same: ' ', added: '+', removed: '−' }
const SAID: Record<DiffLine['kind'], string> = { same: '', added: 'Added: ', removed: 'Removed: ' }

/** The Revisions tab: every saved version, and what changed between any two of them. */
export function Revisions({ templateId }: { templateId: string }) {
  const me = useMe()
  const revisions = $api.useQuery('get', '/api/drafting/templates/{template_id}/revisions', {
    params: { path: { template_id: templateId } },
  })
  const [chosenFrom, setChosenFrom] = useState<string | null>(null)
  const [chosenTo, setChosenTo] = useState<string | null>(null)
  const newestFirst = useMemo(
    () => [...(revisions.data ?? [])].sort((a, b) => b.revision - a.revision),
    [revisions.data],
  )

  if (revisions.isPending) return <Loading what="Loading revisions" />
  if (revisions.isError)
    return (
      <LoadFailed
        what="the revisions"
        error={revisions.error}
        onRetry={() => void revisions.refetch()}
      />
    )
  if (newestFirst.length === 0)
    return (
      <Empty title="No revisions yet">
        A revision is recorded each time the template is saved, and when a letter is started from
        it.
      </Empty>
    )

  const find = (id: string | null) => newestFirst.find((revision) => revision.id === id)
  const to = find(chosenTo) ?? newestFirst[0]
  const from = find(chosenFrom) ?? newestFirst[1] ?? newestFirst[0]

  return (
    <>
      <section>
        <ul className="border-t border-line-2">
          {newestFirst.map((revision) => (
            <li key={revision.id} className="flex items-start gap-3 border-b border-line-2 py-3">
              <div className="min-w-0 flex-1">
                <p className="text-125">
                  <span className="font-semibold">Revision {revision.revision}</span>
                  <span className="text-ink-3">
                    {' '}
                    · {formatInstant(revision.created_at, me.timezone)}
                  </span>
                </p>
                <p className="mt-0.5 text-12">{revision.summary ?? 'No summary was given.'}</p>
                <p className="mt-0.5 text-115 text-ink-3">
                  {revision.created_by_name ?? 'Nobody is recorded as saving it'} ·{' '}
                  {filings(revision.filed_count)}
                </p>
              </div>
            </li>
          ))}
        </ul>
      </section>
      <section className="mt-8">
        {newestFirst.length === 1 ? (
          <>
            <GroupLabel>What revision {to.revision} says</GroupLabel>
            <Lines lines={revisionLines(to.content).map((text) => ({ kind: 'same', text }))} />
          </>
        ) : (
          <>
            <GroupLabel>
              Revision {to.revision} against {from.revision}
            </GroupLabel>
            <div className="mb-3 flex gap-3">
              <Pick label="From" value={from.id} revisions={newestFirst} onPick={setChosenFrom} />
              <Pick label="To" value={to.id} revisions={newestFirst} onPick={setChosenTo} />
            </div>
            <Comparison from={from} to={to} />
          </>
        )}
      </section>
    </>
  )
}

function Pick({
  label,
  value,
  revisions,
  onPick,
}: {
  label: string
  value: string
  revisions: Revision[]
  onPick: (id: string) => void
}) {
  return (
    <div className="min-w-0 flex-1">
      <Field label={label}>
        <Select value={value} onChange={(event) => onPick(event.target.value)}>
          {revisions.map((revision) => (
            <option key={revision.id} value={revision.id}>
              Revision {revision.revision}
              {revision.summary ? ` · ${revision.summary}` : ''}
            </option>
          ))}
        </Select>
      </Field>
    </div>
  )
}

function Comparison({ from, to }: { from: Revision; to: Revision }) {
  const lines = useMemo(
    () => diffLines(revisionLines(from.content), revisionLines(to.content)),
    [from, to],
  )
  const added = lines.filter((line) => line.kind === 'added').length
  const removed = lines.filter((line) => line.kind === 'removed').length
  const count = (n: number) => (n === 1 ? '1 line' : `${n} lines`)
  return (
    <>
      <p className="mb-2 text-125 text-ink-2" role="status">
        {from.id === to.id
          ? 'The same revision is chosen on both sides.'
          : added + removed === 0
            ? `Revisions ${from.revision} and ${to.revision} say the same thing.`
            : `From revision ${from.revision} to revision ${to.revision}: ${count(added)} added, ${count(removed)} removed.`}
      </p>
      <Lines lines={lines} />
    </>
  )
}

function Lines({ lines }: { lines: DiffLine[] }) {
  return (
    <div className="overflow-x-auto rounded-16 border border-line bg-card py-2 text-13 leading-[1.7]">
      {lines.map((line, index) => (
        <div
          // A line has no identity beyond its place: the same text can occur many times.
          key={index}
          className={cx(
            'flex gap-2 px-3',
            line.kind === 'added' && 'bg-ok-tint text-ok',
            line.kind === 'removed' && 'bg-danger-tint text-danger',
          )}
        >
          <span aria-hidden="true" className="w-3 shrink-0 text-center select-none">
            {MARK[line.kind]}
          </span>
          <span className="min-w-0 flex-1 whitespace-pre-wrap">
            <span className="sr-only">{SAID[line.kind]}</span>
            {line.text || ' '}
          </span>
        </div>
      ))}
    </div>
  )
}
