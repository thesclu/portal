import { api, type Schemas } from '../api/client'
import { unwrap } from '../components/ActionDialog'

/**
 * Sending a message the person wrote, or, for an intern, submitting it for approval instead
 * (P20h, 42b: nothing an intern writes leaves the portal without an approver's yes). The server
 * refuses an intern's Send either way; this asks for the right thing first, so every composer
 * (replies, forwards, follow-ups, fix and resend) behaves the same.
 */
export async function sendOrSubmit(
  messageId: string,
  intern: boolean,
): Promise<SentMessage | null> {
  if (intern) {
    unwrap(
      await api.POST('/api/core/reviews', {
        body: { subject_type: 'message', subject_id: messageId, kind: 'approval', note: '' },
      }),
    )
    return null // submitted: nothing was sent
  }
  const message = unwrap(
    await api.POST('/api/mail/messages/{message_id}/send', {
      params: { path: { message_id: messageId } },
    }),
  )
  return message
}

type SentMessage = Schemas['MailMessageOut']

/** What the button says: Send, or for an intern what will actually happen. */
export function sendLabel(intern: boolean, send = 'Send'): string {
  return intern ? 'Submit for approval' : send
}
