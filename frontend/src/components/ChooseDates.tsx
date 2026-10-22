import { useState } from 'react'

import { Dialog } from './Dialog'
import { DialogField, RaisedInput } from './Fields'
import { Button } from './ui'

/**
 * "Choose dates…" (190's span menu, and the Inbox search's "Any time" chip, 154): a From and an
 * Until, both the person's own dates and both optional. Show hands them back as YYYY-MM-DD ('' when
 * left empty).
 */
export function ChooseDates({
  since,
  until,
  onClose,
  onChoose,
}: {
  since: string
  until: string
  onClose: () => void
  onChoose: (since: string, until: string) => void
}) {
  const [from, setFrom] = useState(since)
  const [to, setTo] = useState(until)
  return (
    <Dialog
      title="Choose dates"
      open
      onClose={onClose}
      width={440}
      top={90}
      footer={
        <>
          <Button kind="text" pill onClick={onClose}>
            Cancel
          </Button>
          <Button kind="primary" pill onClick={() => onChoose(from, to)}>
            Show
          </Button>
        </>
      }
    >
      <div className="flex gap-3">
        <div className="min-w-0 flex-1">
          <DialogField label="From">
            <RaisedInput
              compact
              type="date"
              value={from}
              max={to || undefined}
              onChange={(event) => setFrom(event.target.value)}
            />
          </DialogField>
        </div>
        <div className="min-w-0 flex-1">
          <DialogField label="Until">
            <RaisedInput
              compact
              type="date"
              value={to}
              min={from || undefined}
              onChange={(event) => setTo(event.target.value)}
            />
          </DialogField>
        </div>
      </div>
    </Dialog>
  )
}
