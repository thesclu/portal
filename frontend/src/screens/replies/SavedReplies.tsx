import { useState } from 'react'

import { api } from '../../api/client'
import { $api } from '../../api/query'
import { useMe } from '../../app/session'
import { ActionDialog, unwrap, type ActionSpec, type Values } from '../../components/ActionDialog'
import { Empty, LoadFailed, Loading } from '../../components/States'
import { Badge, Button, Notice } from '../../components/ui'
import { formatInstant } from '../../lib/dates'
import { isFullStaff } from '../../lib/roles'
import { useRefreshMail, type SavedReply } from '../inbox/mail'

const text = (values: Values, name: string) => String(values[name] ?? '').trim()
const raw = (values: Values, name: string) => String(values[name] ?? '')

function replySpec(reply?: SavedReply): ActionSpec {
  return {
    key: reply ? `edit-reply-${reply.id}` : 'add-reply',
    title: reply ? 'Edit saved reply' : 'Add saved reply',
    intro: reply
      ? undefined
      : 'Words you send often. In the composer, “Saved replies” puts them where the cursor is.',
    submit: reply ? 'Save' : 'Add',
    fields: [
      { name: 'name', label: 'Name', required: true, initial: reply?.name ?? '' },
      {
        name: 'body',
        label: 'Text',
        type: 'textarea',
        required: true,
        initial: reply?.body ?? '',
        hint: 'Inserted as written, line breaks included.',
      },
    ],
    run: async (values) => {
      const body = { name: text(values, 'name'), body: raw(values, 'body') }
      unwrap(
        reply
          ? await api.PATCH('/api/mail/saved-replies/{reply_id}', {
              params: { path: { reply_id: reply.id } },
              body,
            })
          : await api.POST('/api/mail/saved-replies', { body }),
      )
    },
  }
}

function deleteSpec(reply: SavedReply): ActionSpec {
  return {
    key: `delete-reply-${reply.id}`,
    title: 'Delete saved reply',
    intro: `“${reply.name}” will be deleted. Messages already written with it are not changed.`,
    submit: 'Delete',
    danger: true,
    fields: [],
    run: async () => {
      unwrap(
        await api.DELETE('/api/mail/saved-replies/{reply_id}', {
          params: { path: { reply_id: reply.id } },
        }),
      )
    },
  }
}

/** Settings → Saved replies: the texts the composer offers, and their keeping. */
export function SavedReplies() {
  const me = useMe()
  const refresh = useRefreshMail()
  const [spec, setSpec] = useState<ActionSpec | null>(null)
  const [showArchived, setShowArchived] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  // P20h: everyone who works requests reads the replies; only full staff keep them.
  const keeper = isFullStaff(me.records_role)
  const replies = $api.useQuery('get', '/api/mail/saved-replies', {
    params: { query: { include_archived: showArchived } },
  })

  async function setArchived(reply: SavedReply, archived: boolean) {
    setBusyId(reply.id)
    setError(null)
    try {
      unwrap(
        await api.PATCH('/api/mail/saved-replies/{reply_id}', {
          params: { path: { reply_id: reply.id } },
          body: { archived },
        }),
      )
      await refresh()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Something went wrong. Try again.')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <section>
      <div className="mb-3 flex items-center gap-3">
        <p className="min-w-0 flex-1 text-125 text-ink-2">
          A saved reply is text the team sends often. In the composer, “Saved replies” puts one
          where the cursor is; it can then be changed like anything else typed.
        </p>
        {keeper ? (
          <Button kind="primary" onClick={() => setSpec(replySpec())}>
            Add saved reply
          </Button>
        ) : null}
      </div>
      <label className="mb-2 flex items-center gap-2 text-125">
        <input
          type="checkbox"
          checked={showArchived}
          onChange={(event) => setShowArchived(event.target.checked)}
        />
        Show archived
      </label>
      {error ? (
        <div className="mb-2">
          <Notice tone="danger">{error}</Notice>
        </div>
      ) : null}
      {replies.isPending ? (
        <Loading what="Loading saved replies" />
      ) : replies.isError ? (
        <LoadFailed
          what="the saved replies"
          error={replies.error}
          onRetry={() => void replies.refetch()}
        />
      ) : replies.data.length === 0 ? (
        <Empty title="No saved replies yet">
          Add one and it is offered in the composer from then on.
        </Empty>
      ) : (
        <ul>
          {replies.data.map((reply) => (
            <li key={reply.id} className="border-b border-line-2 py-3">
              <div className="flex items-start gap-3">
                <div className="min-w-0 flex-1">
                  <p className="text-13 font-medium">
                    {reply.name}
                    {reply.archived_at ? (
                      <span className="ml-2">
                        <Badge>Archived</Badge>
                      </span>
                    ) : null}
                  </p>
                  <p className="line-clamp-3 text-125 whitespace-pre-wrap text-ink-2">
                    {reply.body}
                  </p>
                  <p className="mt-1 text-12 text-ink-3">
                    Changed {formatInstant(reply.updated_at, me.timezone)}
                  </p>
                </div>
                <div className={keeper ? 'flex shrink-0 gap-1.5' : 'hidden'}>
                  <Button kind="quiet" onClick={() => setSpec(replySpec(reply))}>
                    Edit
                  </Button>
                  <Button
                    kind="quiet"
                    busy={busyId === reply.id}
                    onClick={() => void setArchived(reply, !reply.archived_at)}
                  >
                    {reply.archived_at ? 'Unarchive' : 'Archive'}
                  </Button>
                  <Button kind="quiet" onClick={() => setSpec(deleteSpec(reply))}>
                    Delete
                  </Button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
      <ActionDialog
        spec={spec}
        onClose={() => setSpec(null)}
        onDone={() => {
          setSpec(null)
          void refresh()
        }}
      />
    </section>
  )
}
