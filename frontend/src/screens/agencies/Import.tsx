import { useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router'

import { api, type Schemas } from '../../api/client'
import { PageHeader } from '../../app/layout'
import { unwrap } from '../../components/ActionDialog'
import { RaisedSelect } from '../../components/Fields'
import { ChevronDown } from '../../components/icons'
import { CardHead, ListCard, ListHead } from '../../components/ListTable'
import { Menu } from '../../components/Menu'
import { Callout, cx } from '../../components/ui'

type Preview = Schemas['AgencyImportPreviewOut']
type Row = Schemas['AgencyImportRowOut']
type Result = Schemas['AgencyImportResultOut']

const COLUMNS = 'minmax(0,1.5fr) minmax(0,1.4fr) minmax(0,1fr) minmax(0,1.4fr)'

/** A preview or import call: the file the person chose, sent as it is, never named on disk. */
function formOf(file: File, extra: Record<string, unknown>): FormData {
  const form = new FormData()
  form.append('file', file)
  for (const [key, value] of Object.entries(extra)) {
    if (value !== undefined) form.append(key, JSON.stringify(value))
  }
  return form
}

/**
 * Import agencies (168; owner's yes, DECISIONS 37c): choose a spreadsheet saved as comma-separated
 * text or an Excel workbook (P27b), match its columns to the directory's fields, read the preview
 * (each row new, already in Agencies, or with something to fix), choose for each match whether to
 * fill in its blank fields, and import. The server reads the file again and writes everything in one go or nothing.
 */
export function ImportAgencies() {
  const navigate = useNavigate()
  const client = useQueryClient()
  const input = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [choices, setChoices] = useState<Record<number, 'skip' | 'update'>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<Result | null>(null)

  async function read(chosen: File, mapping?: Record<string, string | null>) {
    setBusy(true)
    setError(null)
    try {
      const found = unwrap(
        await api.POST('/api/records/agencies/import/preview', {
          body: formOf(chosen, { mapping }) as never,
          bodySerializer: (payload) => payload as unknown as FormData,
        }),
      )
      setPreview(found)
      setChoices({})
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'That file could not be read.')
      setPreview(null)
    } finally {
      setBusy(false)
    }
  }

  async function run() {
    if (!file || !preview) return
    setBusy(true)
    setError(null)
    try {
      const result = unwrap(
        await api.POST('/api/records/agencies/import', {
          body: formOf(file, { mapping: preview.mapping, decisions: choices }) as never,
          bodySerializer: (payload) => payload as unknown as FormData,
        }),
      )
      await client.invalidateQueries({ queryKey: ['get', '/api/records/agencies'] })
      setDone(result)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Nothing was imported.')
    } finally {
      setBusy(false)
    }
  }

  const rows = preview?.rows ?? []
  const updates = rows.filter((row) => row.status === 'match' && choices[row.number] === 'update')
  const count = (preview?.new ?? 0) + updates.length
  const look = (preview?.matches ?? 0) + (preview?.problems ?? 0)
  const labelOf = (key: string) => preview?.fields.find((f) => f.key === key)?.label ?? key

  return (
    <div className="flex flex-col leading-[normal]">
      <PageHeader title="Import agencies" />
      <div className="flex min-w-0 flex-col gap-3.5 px-2 pb-2">
        {done ? (
          <ListCard aria-label="Imported">
            <div className="flex flex-col gap-3.5 px-5 py-[18px]">
              <p className="text-13 text-ink">
                {done.created} {done.created === 1 ? 'agency' : 'agencies'} added
                {done.updated ? `, ${done.updated} filled in` : ''}
                {done.skipped ? `, ${done.skipped} left as they were` : ''}
                {done.problems ? `, ${done.problems} not imported` : ''}.
              </p>
              <div className="flex gap-2">
                <Link
                  to="/agencies"
                  className="inline-flex h-8 items-center rounded-pill bg-brand px-3.5 text-125 font-medium text-white hover:bg-brand-dark hover:text-white"
                >
                  Back to Agencies
                </Link>
              </div>
            </div>
          </ListCard>
        ) : (
          <>
            <ListCard aria-label="The file">
              <div className="flex flex-col gap-3.5 px-5 py-[18px]">
                <label className="flex cursor-pointer items-center gap-2.5 rounded-12 bg-card-white px-3 py-2.5 text-125 shadow-field">
                  <input
                    ref={input}
                    type="file"
                    accept=".csv,.xlsx,text/csv,text/plain,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    className="sr-only"
                    onChange={(event) => {
                      const chosen = event.target.files?.[0]
                      if (!chosen) return
                      setFile(chosen)
                      void read(chosen)
                    }}
                  />
                  <span className={cx('min-w-0 flex-1 truncate', file ? 'text-ink' : 'text-muted')}>
                    {file
                      ? file.name
                      : 'Choose a spreadsheet (comma-separated text or an Excel workbook)…'}
                  </span>
                  {preview ? (
                    <span className="text-muted">
                      {rows.length} {rows.length === 1 ? 'row' : 'rows'}
                    </span>
                  ) : busy ? (
                    <span className="text-muted">Reading…</span>
                  ) : null}
                </label>
                {preview ? (
                  <>
                    <div className="text-105 font-semibold tracking-label text-muted uppercase">
                      Match your columns
                    </div>
                    {preview.headers.map((header) => (
                      <div key={header} className="flex items-center gap-3">
                        <span className="w-[180px] shrink-0 truncate text-125 text-ink-2">
                          {header}
                        </span>
                        <span aria-hidden className="text-16 text-line-control">
                          →
                        </span>
                        <span className="w-[240px] shrink-0">
                          <RaisedSelect
                            compact
                            aria-label={`Read “${header}” as`}
                            value={preview.mapping[header] ?? ''}
                            disabled={busy}
                            onChange={(event) =>
                              file &&
                              void read(file, {
                                ...preview.mapping,
                                [header]: event.target.value || null,
                              })
                            }
                          >
                            <option value="">Not imported</option>
                            {preview.fields.map((field) => (
                              <option key={field.key} value={field.key}>
                                {field.label}
                              </option>
                            ))}
                          </RaisedSelect>
                        </span>
                      </div>
                    ))}
                  </>
                ) : null}
              </div>
            </ListCard>

            {error ? (
              <Callout tone="danger" role="alert">
                {error}
              </Callout>
            ) : null}

            {preview ? (
              <ListCard aria-label="Preview">
                <CardHead
                  title="Preview"
                  count={`${preview.new} ready${look ? ` · ${look} need a look` : ''}`}
                />
                <ListHead columns={COLUMNS} ruled="both">
                  <span>Name</span>
                  <span>Records email</span>
                  <span>Jurisdiction</span>
                  <span>Check</span>
                </ListHead>
                {rows.length === 0 ? (
                  <p className="px-9 py-[11px] text-125 text-muted">The file has no rows.</p>
                ) : (
                  rows.map((row) => (
                    <PreviewRow
                      key={row.number}
                      row={row}
                      choice={choices[row.number] ?? 'skip'}
                      onChoose={(choice) => setChoices((was) => ({ ...was, [row.number]: choice }))}
                      labelOf={labelOf}
                    />
                  ))
                )}
              </ListCard>
            ) : null}

            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => void navigate('/agencies')}
                className="box-border inline-flex h-8 items-center gap-1.5 rounded-9 px-3.5 text-125 font-medium whitespace-nowrap text-ink-2 hover:bg-hover"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={!preview || count === 0 || busy}
                onClick={() => void run()}
                className="box-border inline-flex h-8 items-center gap-1.5 rounded-pill bg-brand px-3.5 text-125 font-medium whitespace-nowrap text-white hover:bg-brand-dark disabled:opacity-55"
              >
                Import {count}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function PreviewRow({
  row,
  choice,
  onChoose,
  labelOf,
}: {
  row: Row
  choice: 'skip' | 'update'
  onChoose: (choice: 'skip' | 'update') => void
  labelOf: (key: string) => string
}) {
  const cell = 'min-w-0 truncate'
  return (
    <div
      role="row"
      style={{ gridTemplateColumns: COLUMNS }}
      className="grid items-center gap-x-3.5 px-9 py-[11px] text-125 leading-[normal] text-ink"
    >
      <span className={cell}>{row.values.name || '—'}</span>
      <span className={cell}>{row.values.default_request_email || '—'}</span>
      <span className={cell}>{row.values.jurisdiction || '—'}</span>
      <span className={cx(cell, 'flex items-center gap-1.5')}>
        {row.status === 'new' ? (
          <>
            <span className="text-ok">Ready</span>
            {row.values.filing_method ? null : (
              <span className="truncate text-muted">· no email or form yet</span>
            )}
          </>
        ) : row.status === 'problem' ? (
          <span className="truncate text-warn" title={row.problems.join('; ')}>
            {row.problems.join('; ')}
          </span>
        ) : (
          <>
            <span className="shrink-0 text-muted" title={row.match_agency_name ?? undefined}>
              Already in Agencies
            </span>
            {row.fills.length > 0 ? (
              <Menu
                label={`Row ${row.number}: what to do`}
                width={240}
                items={[
                  {
                    label: 'Leave it as it is',
                    current: choice === 'skip',
                    onPick: () => onChoose('skip'),
                  },
                  {
                    label: `Fill in ${row.fills.map(labelOf).join(', ').toLowerCase()}`,
                    current: choice === 'update',
                    onPick: () => onChoose('update'),
                  },
                ]}
              >
                <span className="inline-flex items-center gap-1 text-ink-2">
                  · {choice === 'update' ? 'fill in blanks' : 'skip'}
                  <ChevronDown size={9} width={2.6} stroke="var(--color-muted)" />
                </span>
              </Menu>
            ) : (
              <span className="truncate text-muted">· nothing to fill in</span>
            )}
          </>
        )}
      </span>
    </div>
  )
}
