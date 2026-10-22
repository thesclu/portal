import type { Schemas } from '../../api/client'
import type { ActionSpec } from '../../components/ActionDialog'
import { Box, DialogField } from '../../components/Fields'
import { cx } from '../../components/ui'
import { TIME_SPANS } from '../inbox/model'

type Row = Schemas['DocketRowOut']

export type ExportFormat = 'csv' | 'json' | 'pdf'

export interface ExportChoice {
  format: ExportFormat
  /** The person's own dates, both inclusive: requests made between them (P23b). */
  since?: string
  until?: string
}

const FORMATS: Array<{ value: ExportFormat; label: string }> = [
  { value: 'csv', label: 'CSV' },
  { value: 'json', label: 'JSON' },
  { value: 'pdf', label: 'PDF summary' },
]

/** `YYYY-MM-DD`, `days` before today. */
function before(days: number): string {
  const when = new Date()
  when.setDate(when.getDate() - days)
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${when.getFullYear()}-${pad(when.getMonth() + 1)}-${pad(when.getDate())}`
}

/** The dates a "Date range" choice asks for: all time, a span back from today, or the dates typed. */
export function exportSpan(
  range: string,
  from: string,
  to: string,
): { since?: string; until?: string } {
  if (range === 'custom') return { since: from || undefined, until: to || undefined }
  const span = TIME_SPANS.find((s) => s.key === range)
  return span ? { since: before(span.days) } : {}
}

/**
 * Export the registry (043), opened from the Docket's Export on the rows selected: the format, what
 * goes in, the dates, then the file. P23b: CSV, JSON or a PDF summary (the same fields as a plain
 * table), over the requests made in the range chosen. Only what the server writes is offered under
 * Include: the requests and their current status; the events, status history, audit log and files
 * the design lists are not drawn, since the export does not carry them.
 */
export function exportRegistry(
  rows: Row[],
  run: (rows: Row[], choice: ExportChoice) => Promise<void>,
): ActionSpec {
  return {
    key: `export-${rows.length}`,
    title: 'Export the registry',
    subtitle: `${rows.length} ${rows.length === 1 ? 'request' : 'requests'} · the ones selected`,
    width: 480,
    top: 80,
    compact: true,
    submit: 'Export',
    fields: [
      {
        name: 'format',
        label: 'Format',
        type: 'custom',
        initial: 'csv',
        render: (value, onChange) => (
          <DialogField label="Format">
            <span role="radiogroup" aria-label="Format" className="flex gap-2">
              {FORMATS.map((format) => {
                const chosen = (value || 'csv') === format.value
                return (
                  <button
                    key={format.value}
                    type="button"
                    role="radio"
                    aria-checked={chosen}
                    onClick={() => onChange(format.value)}
                    className={cx(
                      'rounded-9 text-125 whitespace-nowrap',
                      chosen
                        ? 'bg-brand px-3.5 py-2 font-semibold text-white'
                        : 'px-[13px] py-[7px] font-medium text-ink-2 hover:bg-hover',
                    )}
                  >
                    {format.label}
                  </button>
                )
              })}
            </span>
          </DialogField>
        ),
      },
      {
        name: 'include',
        label: 'Include',
        type: 'custom',
        render: () => (
          <div className="flex flex-col gap-[9px]">
            <span className="text-125 leading-[normal] text-ink-2">Include</span>
            <span className="flex items-center gap-2.5 text-13 text-ink">
              <Box checked />
              Requests and their current status
            </span>
          </div>
        ),
      },
      {
        name: 'range',
        label: 'Date range',
        type: 'select',
        initial: 'all',
        options: [
          { value: 'all', label: 'All time' },
          ...TIME_SPANS.map((span) => ({ value: span.key, label: span.label })),
          { value: 'custom', label: 'Choose dates…' },
        ],
      },
      {
        name: 'from',
        label: 'Made from',
        type: 'date',
        row: 'dates',
        when: (values) => values.range === 'custom',
      },
      {
        name: 'to',
        label: 'Until',
        type: 'date',
        row: 'dates',
        when: (values) => values.range === 'custom',
      },
    ],
    run: (values) =>
      run(rows, {
        format: String(values.format || 'csv') as ExportFormat,
        ...exportSpan(
          String(values.range ?? 'all'),
          String(values.from ?? ''),
          String(values.to ?? ''),
        ),
      }),
  }
}
