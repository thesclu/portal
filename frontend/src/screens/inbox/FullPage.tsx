import { useState } from 'react'
import { useNavigate, useParams } from 'react-router'

import { $api } from '../../api/query'
import { PageHeader } from '../../app/layout'
import { Loading, LoadFailed } from '../../components/States'
import { subjectOf, type Bucket } from './model'
import { Reader, type Afterwards } from './Reader'
import { Viewer } from './Viewer'

/**
 * A message on a page of its own ("Open full page", 107; P23b): the reading pane at the content's
 * width, read by the message's id (`/records/inbox/{id}`), so a link to it opens whatever list it
 * is in now. The way back is the list it is in, with it open there. Nothing here differs from the
 * pane in the Inbox: the same header, next step, words, files, notes and reply.
 */
export function InboxMessagePage() {
  const { sourceId = '' } = useParams()
  const navigate = useNavigate()
  const item = $api.useQuery(
    'get',
    '/api/records/inbox/{source_id}',
    { params: { path: { source_id: sourceId } } },
    { enabled: Boolean(sourceId), retry: false },
  )
  const [afterwards, setAfterwards] = useState<Afterwards | null>(null)
  const [file, setFile] = useState<string | null>(null)
  const [page, setPage] = useState(1)

  if (item.isPending) return <Loading what="the message" />
  if (item.isError || !item.data)
    return <LoadFailed what="the message" error={item.error} onRetry={() => void item.refetch()} />
  const row = item.data
  const bucket: Bucket = row.buckets.includes('flagged')
    ? 'flagged'
    : row.buckets.includes('new')
      ? 'new'
      : 'all'
  const list = `/inbox?bucket=${bucket}`
  const open = row.attachments.find((a) => a.id === file)
  return (
    <div className="flex min-h-0 flex-1 flex-col leading-[normal]">
      <PageHeader
        title={subjectOf(row)}
        back={{
          label: bucket === 'flagged' ? 'Flagged' : bucket === 'new' ? 'New' : 'All mail',
          to: `${list}&message=${row.source.id}`,
        }}
      />
      <div className="flex min-h-0 min-w-0 flex-1 items-stretch gap-3.5 px-2 pb-2">
        <Reader
          key={row.source.id}
          className={open ? 'flex-[0_0_420px]' : undefined}
          row={row}
          bucket={bucket}
          afterwards={afterwards}
          onAfterwards={setAfterwards}
          onOpenFile={(id) => {
            setFile(id)
            setPage(1)
          }}
          onNext={() => void navigate(list)}
          onClose={() => void navigate(`${list}&message=${row.source.id}`)}
        />
        {open ? (
          <Viewer file={open} page={page} onPage={setPage} onClose={() => setFile(null)} />
        ) : null}
      </div>
    </div>
  )
}
