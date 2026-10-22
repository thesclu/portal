/** The words of one footnote, new or being changed: a dialog in the v4 look (a raised field,
 *  text Cancel and the pill primary), as every dialog since P20c. */
import { useState } from 'react'

import { Dialog } from '../../../components/Dialog'
import { DialogField, RaisedTextarea } from '../../../components/Fields'
import { Button } from '../../../components/ui'

export function FootnoteDialog({
  open,
  initial,
  marker,
  onClose,
  onSubmit,
}: {
  open: boolean
  initial: string
  marker: string
  onClose: () => void
  onSubmit: (text: string) => void
}) {
  const [text, setText] = useState(initial)
  const value = text.trim()
  return (
    <Dialog
      title={`Footnote ${marker}`}
      open={open}
      onClose={onClose}
      footer={
        <>
          <Button kind="text" pill onClick={onClose}>
            Cancel
          </Button>
          <Button kind="primary" pill disabled={!value} onClick={() => onSubmit(value)}>
            {initial ? 'Save' : 'Add footnote'}
          </Button>
        </>
      }
    >
      <DialogField label="Citation">
        <RaisedTextarea
          minHeight={96}
          value={text}
          data-autofocus
          onChange={(event) => setText(event.target.value)}
        />
      </DialogField>
    </Dialog>
  )
}
