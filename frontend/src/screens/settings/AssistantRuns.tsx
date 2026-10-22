/**
 * Settings → Organisation → Assistant runs (P19d, admins): the sent-data log. One row per call
 * to the assistant: when, which task and tier, the provider and model, who asked, how it ended,
 * the tokens, and what was sent as "part: N chars" chips (the backend keeps sizes and hashes,
 * not the text, unless "Keep prompts" is on).
 */
import { $api } from '../../api/query'
import { useMe } from '../../app/session'
import { LoadFailed, Loading } from '../../components/States'
import { Badge } from '../../components/ui'
import { chipText, providerLabel, summaryChips, tokensLabel } from '../../lib/assistant'
import { formatInstant } from '../../lib/dates'

const LIMIT = 100

function tone(status: string): 'ok' | 'danger' | 'warn' | 'neutral' {
  if (status === 'ok' || status === 'succeeded') return 'ok'
  if (status === 'failed' || status === 'error') return 'danger'
  if (status === 'refused' || status === 'off') return 'warn'
  return 'neutral'
}

export function AssistantRuns() {
  const me = useMe()
  const runs = $api.useQuery('get', '/api/core/ai/runs', { params: { query: { limit: LIMIT } } })
  if (runs.isPending) return <Loading what="Loading the runs" />
  if (runs.isError)
    return (
      <LoadFailed
        what="the assistant runs"
        error={runs.error}
        onRetry={() => void runs.refetch()}
      />
    )
  if (runs.data.length === 0)
    return (
      <p className="py-3 text-125 text-ink-3">The assistant has not been asked anything yet.</p>
    )
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left">
        <thead>
          <tr className="border-b border-line text-105 tracking-[0.06em] text-ink-3 uppercase">
            <th className="py-2 pr-3 font-semibold">When</th>
            <th className="py-2 pr-3 font-semibold">Task</th>
            <th className="py-2 pr-3 font-semibold">Provider</th>
            <th className="py-2 pr-3 font-semibold">Who</th>
            <th className="py-2 pr-3 font-semibold">Status</th>
            <th className="py-2 pr-3 font-semibold">Tokens</th>
            <th className="py-2 font-semibold">Sent</th>
          </tr>
        </thead>
        <tbody>
          {runs.data.map((run) => (
            <tr key={run.id} className="border-b border-line-2 align-top text-125">
              <td className="py-2.5 pr-3 whitespace-nowrap text-ink-2">
                {formatInstant(run.created_at, me.timezone)}
              </td>
              <td className="py-2.5 pr-3">
                <span className="block">{run.task}</span>
                <span className="block text-12 text-ink-3">
                  {run.tier}
                  {run.subject_type ? ` · ${run.subject_type}` : ''}
                </span>
              </td>
              <td className="py-2.5 pr-3 text-ink-2">{providerLabel(run.provider, run.model)}</td>
              <td className="py-2.5 pr-3">{run.requested_by_name ?? 'System'}</td>
              <td className="py-2.5 pr-3">
                <Badge tone={tone(run.status)}>{run.status}</Badge>
                {run.error ? <span className="block text-12 text-danger">{run.error}</span> : null}
              </td>
              <td className="py-2.5 pr-3 whitespace-nowrap text-ink-2">
                {tokensLabel(run.tokens_in, run.tokens_out)}
              </td>
              <td className="py-2.5">
                <span className="flex flex-wrap gap-1">
                  {summaryChips(run.context_summary).map((chip) => (
                    <Badge key={chip.part}>{chipText(chip)}</Badge>
                  ))}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
