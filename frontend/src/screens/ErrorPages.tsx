import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, isRouteErrorResponse, useRouteError } from 'react-router'

import { api, type Schemas } from '../api/client'
import { $api } from '../api/query'
import { PageHeader } from '../app/layout'
import { landing } from '../app/nav'
import { useMaybeMe } from '../app/session'
import { ActionDialog, unwrap, type ActionSpec } from '../components/ActionDialog'
import { Empty } from '../components/States'
import { Button, Callout } from '../components/ui'
import { formatInstant } from '../lib/dates'

type Ask = Schemas['AccessRequestOut']

/** A text button that goes somewhere: "Back to Docket". */
function TextLink({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <Link
      to={to}
      className="inline-flex h-8 items-center rounded-9 px-[14px] text-125 font-medium whitespace-nowrap text-ink-2 hover:bg-hover"
    >
      {children}
    </Link>
  )
}

function PillLink({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <Link
      to={to}
      className="inline-flex h-8 items-center rounded-pill bg-brand px-[14px] text-125 font-medium whitespace-nowrap text-white hover:bg-brand-dark"
    >
      {children}
    </Link>
  )
}

/** The page under the title row, as 225–227 draw it: the content's 8 px sides and foot, the
 *  state centred in what is left, on the browser's normal line height. */
function StateBody({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-3.5 px-2 pb-2 leading-[normal]">
      {children}
    </div>
  )
}

function askSpec(requestId: string, trackingNumber: string | null | undefined): ActionSpec {
  return {
    key: `ask-${requestId}`,
    title: 'Ask for access',
    subtitle: trackingNumber ? `${trackingNumber} · privileged` : 'privileged',
    width: 440,
    top: 90,
    submit: 'Send request',
    fields: [
      {
        name: 'reason',
        label: 'Why you need it',
        type: 'textarea',
        required: true,
        minHeight: 52,
        autoFocus: true,
      },
    ],
    run: async (values) => {
      unwrap(
        await api.POST('/api/records/requests/{request_id}/access-requests', {
          params: { path: { request_id: requestId } },
          body: { reason: String(values.reason ?? '').trim() },
        }),
      )
    },
  }
}

/**
 * A privileged request the person is not on the list of (design 211). The API sends the
 * tracking number and nothing else; neither does this page. With the request's id (the route
 * has it) the person may ask for access, and sees what became of an earlier ask (P13).
 */
export function NoAccess({
  trackingNumber,
  requestId,
}: {
  trackingNumber?: string | null
  requestId?: string
}) {
  const me = useMaybeMe()
  const client = useQueryClient()
  const [spec, setSpec] = useState<ActionSpec | null>(null)
  const [justAsked, setJustAsked] = useState(false)
  const mine = $api.useQuery(
    'get',
    '/api/records/access-requests/mine',
    {},
    { enabled: Boolean(requestId) },
  )
  const ask: Ask | undefined = requestId
    ? (mine.data ?? [])
        .filter((row) => row.request_id === requestId)
        .sort((a, b) => b.created_at.localeCompare(a.created_at))[0]
    : undefined
  const pending = requestId && ((justAsked && (!ask || !ask.decision)) || (ask && !ask.decision))

  return (
    <>
      <PageHeader title="Docket" />
      <StateBody>
        <Empty
          full
          title="You don’t have access to this request"
          actions={
            <>
              <TextLink to={landing(me)}>Back to Docket</TextLink>
              {!requestId ? null : pending ? null : ask?.decision === 'allowed' ? (
                <Button kind="pill" onClick={() => window.location.reload()}>
                  Open it
                </Button>
              ) : (
                <Button
                  kind="pill"
                  disabled={mine.isPending}
                  onClick={() => setSpec(askSpec(requestId, trackingNumber))}
                >
                  {ask?.decision === 'declined' ? 'Ask again' : 'Ask for access'}
                </Button>
              )}
            </>
          }
        >
          {trackingNumber ?? 'This request'} is privileged. Only the people it’s shared with can
          open it.
        </Empty>
      </StateBody>
      {requestId && (pending || ask?.decision) ? (
        <div className="mx-auto -mt-2 mb-8 w-full max-w-[420px]">
          {pending ? (
            <Callout tone="grey" role="status">
              {ask && !justAsked && me
                ? `You asked on ${formatInstant(ask.created_at, me.timezone)}. Someone who can see it will answer.`
                : 'Asked. Someone who can see it will answer.'}
            </Callout>
          ) : ask?.decision === 'allowed' ? (
            <Callout tone="grey" role="status">
              Your ask was allowed{ask.decision_note ? `: ${ask.decision_note}` : '.'}
            </Callout>
          ) : ask?.decision === 'declined' ? (
            <Callout tone="amber" role="status">
              Your ask was declined{ask.decision_note ? `: ${ask.decision_note}` : '.'}
            </Callout>
          ) : null}
        </div>
      ) : null}
      <ActionDialog
        spec={spec}
        onClose={() => setSpec(null)}
        onDone={() => {
          setSpec(null)
          setJustAsked(true)
          void client.invalidateQueries({ queryKey: ['get', '/api/records/access-requests/mine'] })
        }}
      />
    </>
  )
}

/** Design 212. */
export function NotFound() {
  const me = useMaybeMe()
  return (
    <>
      <PageHeader title="Not found" />
      <StateBody>
        <Empty
          full
          title="This page doesn’t exist"
          actions={<PillLink to={landing(me)}>Go home</PillLink>}
        >
          The link may be old, or the request may have a new address.
        </Empty>
      </StateBody>
    </>
  )
}

/** The router's error boundary (design 213): a screen threw while rendering. */
export function SomethingWentWrong() {
  const error = useRouteError()
  if (isRouteErrorResponse(error) && error.status === 404) return <NotFound />
  return (
    <>
      <PageHeader title="Something went wrong" />
      <StateBody>
        <Empty
          full
          title="We couldn’t load this"
          actions={
            <Button kind="pill" onClick={() => window.location.reload()}>
              Try again
            </Button>
          }
        >
          Nothing you entered was lost. Try again, or check System in Settings if it keeps
          happening.
        </Empty>
      </StateBody>
    </>
  )
}
