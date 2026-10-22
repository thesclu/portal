import { useQueries, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router'

import { api } from '../../api/client'
import { $api } from '../../api/query'
import { PageHeader } from '../../app/layout'
import { ActionDialog, unwrap, type ActionSpec } from '../../components/ActionDialog'
import { LoadFailed, Loading } from '../../components/States'
import { Button } from '../../components/ui'
import { DraftEditor } from './editor/DraftEditor'
import { ForkLetter, ForkSwitchedOff, useFork } from './ForkLetter'
import { AgenciesStage } from './stages/Agencies'
import { FilingResults } from './stages/Results'
import { ReviewFileStage } from './stages/ReviewFile'
import { defaultStage, requestsLabel, type StepKey } from './stages/stages'
import { Stepper } from './stages/Stepper'

const STAGES = ['draft', 'agencies', 'file', 'filed'] as const
type Stage = (typeof STAGES)[number]
const DRAFT_KEY = ['get', '/api/drafting/drafts/{draft_id}']

/** New request, stages 2 to 4 and the filing results (045–052, 060) over one draft. Stage 2 is
 *  the editor (editor/DraftEditor.tsx); stages 3 and 4 and the results live under stages/. A
 *  draft opened with no stage goes to the results once every agency has its tracking number,
 *  else to the editor. Why a filing failed is kept here, across stage 4 and the results: this
 *  session's refusals, and each pending email agency's filing mail that failed. */
export function DraftPage() {
  const { draftId = '' } = useParams()
  const navigate = useNavigate()
  const client = useQueryClient()
  const [params, setParams] = useSearchParams()
  const draft = $api.useQuery('get', '/api/drafting/drafts/{draft_id}', {
    params: { path: { draft_id: draftId } },
  })
  const agencies = $api.useQuery('get', '/api/records/agencies')
  // P21c: a letter written in the drafting tool is framed from it in stage 2.
  const fork = useFork()
  const [spec, setSpec] = useState<ActionSpec | null>(null)
  const [sessionFailures, setSessionFailures] = useState<Record<string, string>>({})
  const mailing = (draft.data?.recipients ?? []).filter(
    (r) => r.status === 'pending' && r.request_id && r.channel === 'email',
  )
  const mail = useQueries({
    queries: mailing.map((r) =>
      $api.queryOptions('get', '/api/mail/messages', {
        params: { query: { request_id: r.request_id ?? '', bucket: 'all', limit: 5 } },
      }),
    ),
  })
  const failures: Record<string, string> = {}
  mailing.forEach((recipient, index) => {
    const filing = (mail[index]?.data?.items ?? []).find((m) => m.kind === 'filing')
    if (filing?.status === 'failed')
      failures[recipient.id] = filing.error ?? 'the provider refused it'
  })
  Object.assign(failures, sessionFailures)
  const onFailure = (id: string, reason: string | null) =>
    setSessionFailures((all) => {
      const next = { ...all }
      if (reason) next[id] = reason
      else delete next[id]
      return next
    })

  const refresh = async () => {
    setSpec(null)
    await client.invalidateQueries({ queryKey: DRAFT_KEY })
    await client.invalidateQueries({ queryKey: ['get', '/api/drafting/drafts/{draft_id}/preview'] })
    await client.invalidateQueries({
      queryKey: ['get', '/api/drafting/recipients/{recipient_id}/preview'],
    })
  }
  const go = (key: StepKey) => {
    if (key === 'start') void navigate('/new')
    else setParams({ stage: key }, { replace: true })
  }

  if (draft.isPending || agencies.isPending) return <Loading what="Opening the letter" />
  if (draft.isError)
    return (
      <LoadFailed what="this letter" error={draft.error} onRetry={() => void draft.refetch()} />
    )
  if (agencies.isError)
    return (
      <LoadFailed
        what="the agencies"
        error={agencies.error}
        onRetry={() => void agencies.refetch()}
      />
    )
  const asked = params.get('stage')
  const stage: Stage = STAGES.some((s) => s === asked)
    ? (asked as Stage)
    : defaultStage(draft.data.recipients)
  const nothingOut = draft.data.recipients.every((r) => !r.request_id)
  const discard: ActionSpec = {
    key: 'discard',
    title: 'Discard this letter',
    intro: 'The draft is deleted. Nothing has been filed from it.',
    submit: 'Discard',
    danger: true,
    fields: [],
    run: async () => {
      unwrap(
        await api.DELETE('/api/drafting/drafts/{draft_id}', {
          params: { path: { draft_id: draftId } },
        }),
      )
      void navigate('/new')
    },
  }

  // Stage 4 names the agency being filed with in the header (049): "<Agency> REQ-… reserved";
  // with none open (052), the letter and how many requests it makes.
  const current =
    stage === 'file'
      ? (draft.data.recipients.find((r) => r.id === params.get('agency')) ?? null)
      : null
  const currentAgency = current
    ? agencies.data.find((a) => a.id === current.agency_id)?.name
    : undefined
  const live = draft.data.recipients.filter((r) => r.status !== 'skipped').length
  const subject = draft.data.subject || 'Untitled letter'
  const header =
    stage === 'file' ? (
      <PageHeader
        title={currentAgency ?? subject}
        back={{ label: 'New request', to: '/new' }}
        roomy
        note={
          current
            ? current.request_id
              ? `${current.tracking_number} ${current.status === 'filed' ? 'filed' : 'reserved'}`
              : 'not reserved yet'
            : requestsLabel(live)
        }
      />
    ) : stage === 'filed' ? (
      <PageHeader title={`${subject} · filed`} />
    ) : (
      <PageHeader
        title="New request"
        actions={
          nothingOut ? (
            <Button kind="text" onClick={() => setSpec(discard)}>
              Discard letter
            </Button>
          ) : undefined
        }
      />
    )

  return (
    <>
      {header}
      {stage === 'draft' ? (
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="px-2 pb-3.5">
            <Stepper current="draft" onGo={go} />
          </div>
          {draft.data.fork_master_document_id ? (
            fork.data?.in_use ? (
              <ForkLetter
                key={draft.data.id}
                draft={draft.data}
                agencies={agencies.data}
                web={fork.data.web_url ?? ''}
                origin={fork.data.web_origin ?? ''}
                onNext={() => setParams({ stage: 'agencies' }, { replace: true })}
              />
            ) : fork.isPending ? null : (
              <ForkSwitchedOff />
            )
          ) : (
            <DraftEditor
              draft={draft.data}
              agencies={agencies.data}
              onNext={() => setParams({ stage: 'agencies' }, { replace: true })}
            />
          )}
        </div>
      ) : stage === 'agencies' ? (
        <AgenciesStage
          draft={draft.data}
          agencies={agencies.data}
          setSpec={setSpec}
          refresh={refresh}
          onGo={go}
        />
      ) : stage === 'file' ? (
        <ReviewFileStage
          draft={draft.data}
          agencies={agencies.data}
          failures={failures}
          onFailure={onFailure}
          setSpec={setSpec}
          onGo={go}
        />
      ) : (
        <FilingResults
          draft={draft.data}
          agencies={agencies.data}
          failures={failures}
          onOpen={(id) => setParams({ stage: 'file', agency: id }, { replace: true })}
        />
      )}
      <ActionDialog spec={spec} onClose={() => setSpec(null)} onDone={() => void refresh()} />
    </>
  )
}
