/** The Citations tab: every footnote of the template as a plain table, over the same rows the
 *  Letter tab holds, so a change here is saved with the letter. */
import type { ActionSpec } from '../../components/ActionDialog'
import { Empty } from '../../components/States'
import { Button } from '../../components/ui'
import { nextMarker } from '../newrequest/editor/letter'
import type { LetterDocument } from '../newrequest/editor/useLetterDocument'
import { confirm, raw, text } from './shared'

function addCitation(doc: LetterDocument): ActionSpec {
  return {
    key: 'add-citation',
    title: 'Add citation',
    submit: 'Add citation',
    fields: [
      {
        name: 'marker',
        label: 'Marker',
        required: true,
        initial: nextMarker(doc.citations),
        hint: 'What the letter prints as the footnote’s number.',
      },
      { name: 'citation_text', label: 'Text', type: 'textarea', required: true },
      {
        name: 'item_key',
        label: 'Belongs to',
        type: 'select',
        initial: '',
        options: [
          { value: '', label: 'The letter as a whole' },
          ...doc.pieces
            .filter((piece) => piece.kind === 'item')
            .map((piece) => ({ value: piece.key, label: `Item ${piece.item_number}` })),
        ],
      },
    ],
    run: async (values) => {
      doc.addCitation(raw(values, 'citation_text'), text(values, 'item_key') || null, {
        marker: text(values, 'marker'),
      })
    },
  }
}

export function Citations({
  doc,
  onAction,
}: {
  doc: LetterDocument
  onAction: (spec: ActionSpec) => void
}) {
  const numberOf = new Map(doc.pieces.map((piece) => [piece.key, piece.item_number]))
  return (
    <section>
      {doc.citations.length === 0 ? (
        <Empty title="No citations">
          A citation is a footnote: its number goes in the text, and its words print under a rule at
          the foot of the letter.
        </Empty>
      ) : (
        <table className="w-full border-collapse text-125">
          <thead>
            <tr className="border-b border-line text-left text-105 font-semibold tracking-[0.08em] text-ink-3 uppercase">
              <th className="py-2 pr-3 font-semibold">Marker</th>
              <th className="py-2 pr-3 font-semibold">Citation</th>
              <th className="py-2 pr-3 font-semibold">Belongs to</th>
              <th className="py-2">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {doc.citations.map((citation) => (
              <tr key={citation.key} className="border-b border-line-2 align-top">
                <td className="py-2.5 pr-3 font-semibold">{citation.marker}</td>
                <td className="py-2.5 pr-3 whitespace-pre-wrap">{citation.citation_text}</td>
                <td className="py-2.5 pr-3 text-ink-2">
                  {citation.item_key && numberOf.has(citation.item_key)
                    ? `Item ${numberOf.get(citation.item_key)}`
                    : 'The letter as a whole'}
                </td>
                <td className="py-1.5 text-right">
                  <Button
                    kind="quiet"
                    aria-label={`Remove citation ${citation.marker}`}
                    onClick={() =>
                      onAction(
                        confirm({
                          key: `remove-citation-${citation.key}`,
                          title: 'Remove citation',
                          intro: `Footnote ${citation.marker} will be removed from this template. A reference to it in the text stays as it is.`,
                          submit: 'Remove',
                          danger: true,
                          run: async () => doc.removeCitation(citation.key),
                        }),
                      )
                    }
                  >
                    Remove
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="mt-3">
        <Button kind="primary" onClick={() => onAction(addCitation(doc))}>
          Add citation
        </Button>
      </div>
    </section>
  )
}
