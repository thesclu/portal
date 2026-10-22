import { useState } from 'react'

import { isAdmin, useMe } from '../../app/session'
import { Callout } from '../../components/ui'
import {
  BoolRow,
  NumberRow,
  Row,
  SensitivityRow,
  SettingsCard,
  SettingsTab,
  TextAreaRow,
  TextRow,
} from './rows'

/**
 * Settings → Filing & deadlines (197): the follow-ups, productions and new requests as drawn,
 * then what the design leaves out but an administrator still sets here (DECISIONS 23): whether a
 * follow-up is sent and what it says, and how far ahead Home looks. Staff see it; an admin edits
 * it. The tracking number is given out by the database and has no Edit. "Who can file without
 * review" (197) is gone with the review switch it read (P20h, 42d): the role decides filing.
 */
export function Filing() {
  const me = useMe()
  const editable = isAdmin(me)
  const [year] = useState(() => new Date().getFullYear())
  return (
    <SettingsTab>
      {(byKey) => {
        const days = (key: string, label: string) => {
          const setting = byKey.get(key)
          return setting ? (
            <NumberRow
              key={key}
              setting={setting}
              label={label}
              unit="days"
              min={1}
              editable={editable}
            />
          ) : null
        }
        const sendFollowups = byKey.get('records.followups_send_enabled')
        const followupSubject = byKey.get('records.followup_subject')
        const followupBody = byKey.get('records.followup_body')
        const sensitivity = byKey.get('records.default_sensitivity')
        return (
          <>
            {editable ? null : <Callout>Only an admin can change these.</Callout>}
            <SettingsCard title="Follow-ups">
              {days('records.followup_first_delay_days', 'First follow-up after a missed deadline')}
              {days('records.followup_interval_days', 'Then every')}
            </SettingsCard>
            <SettingsCard title="Productions">
              {days('records.production_review_window_days', 'Mark a production unreviewed after')}
            </SettingsCard>
            <SettingsCard title="New requests">
              <Row
                label="Tracking number prefix"
                hint={`REQ-${year}- and the next number, given out in order`}
                value="REQ"
                action={null}
              />
              {sensitivity ? <SensitivityRow setting={sensitivity} editable={editable} /> : null}
            </SettingsCard>
            {sendFollowups || followupSubject || followupBody ? (
              <SettingsCard title="Sending follow-ups">
                {sendFollowups ? (
                  <BoolRow
                    setting={sendFollowups}
                    label="Send follow-ups to the agency"
                    on="Each follow-up goes to the agency as soon as it is due, with no undo window."
                    off="A follow-up only opens a task with the draft; nothing is sent."
                    editable={editable}
                  />
                ) : null}
                {followupSubject ? (
                  <TextRow
                    setting={followupSubject}
                    label="Subject"
                    hint={followupSubject.description}
                    editable={editable}
                  />
                ) : null}
                {followupBody ? (
                  <TextAreaRow
                    setting={followupBody}
                    label="Message"
                    hint={followupBody.description}
                    editable={editable}
                  />
                ) : null}
              </SettingsCard>
            ) : null}
            {byKey.get('records.due_soon_window_days') ? (
              <SettingsCard title="Home">
                {days('records.due_soon_window_days', 'Due soon means within')}
              </SettingsCard>
            ) : null}
          </>
        )
      }}
    </SettingsTab>
  )
}
