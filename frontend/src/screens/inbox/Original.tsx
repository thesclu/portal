/**
 * Show original (152): the message's headers as it carried them, eleven at first and the rest
 * on request, with Copy and the download of the untouched original.
 */
import { useState } from 'react'

import { $api } from '../../api/query'

const FIRST = 11

export function Original({ sourceId, onBack }: { sourceId: string; onBack: () => void }) {
  const message = $api.useQuery(
    'get',
    '/api/records/sources/{source_id}/message',
    { params: { path: { source_id: sourceId } } },
    { staleTime: 5 * 60_000 },
  )
  const [all, setAll] = useState(false)
  const [copied, setCopied] = useState(false)
  const headers = message.data?.headers ?? []
  const shown = all ? headers : headers.slice(0, FIRST)
  const text = headers.map((h) => `${h.name}: ${h.value}`).join('\n')
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <div className="flex items-center gap-2.5 border-b border-hairline px-[22px] py-3.5">
        <button type="button" onClick={onBack} className="text-125 font-medium text-brand">
          ← Back to message
        </button>
        <span className="ml-auto flex gap-2">
          <button
            type="button"
            disabled={!text}
            onClick={() => {
              void navigator.clipboard?.writeText(text)
              setCopied(true)
            }}
            className="rounded-9 px-[11px] py-[5px] text-115 font-medium text-ink-2 hover:bg-hover"
          >
            {copied ? 'Copied' : 'Copy'}
          </button>
          <a
            href={`/api/records/sources/${sourceId}/raw`}
            className="rounded-9 px-[11px] py-[5px] text-115 font-medium text-ink-2 hover:bg-hover hover:text-ink-2"
          >
            Download original
          </a>
        </span>
      </div>
      <div className="flex-1 px-[22px] py-4">
        <div className="mb-2.5 text-11 font-semibold tracking-group text-muted uppercase">
          Original message · {headers.length} {headers.length === 1 ? 'header' : 'headers'}
        </div>
        {message.isPending ? (
          <p className="text-125 text-muted">Reading the original…</p>
        ) : !message.data?.readable ? (
          <p className="text-125 text-muted">
            This is not a message the portal can read. Download the original to see it.
          </p>
        ) : (
          <div className="rounded-10 border border-fill-muted bg-surface-soft px-4 py-3.5 text-115 leading-[1.75] break-words text-ink-2">
            {shown.map((header, index) => (
              <div key={`${header.name}-${index}`}>
                <span className="font-medium text-ink">{header.name}:</span> {header.value}
              </div>
            ))}
            {!all && headers.length > FIRST ? (
              <button type="button" onClick={() => setAll(true)} className="mt-1.5 text-muted">
                + {headers.length - FIRST} more
              </button>
            ) : null}
          </div>
        )}
      </div>
    </div>
  )
}
