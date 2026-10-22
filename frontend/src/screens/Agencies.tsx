import { useQueryClient } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'

import { type Schemas } from '../api/client'
import { $api } from '../api/query'
import { PageHeader } from '../app/layout'
import { isAdmin, useMe } from '../app/session'
import { ActionDialog, type ActionSpec } from '../components/ActionDialog'
import { ChevronDown, MoreIcon, SearchIcon } from '../components/icons'
import { CardAction, CardHead, InfoRow, ListCard, ListHead, ListRow } from '../components/ListTable'
import { Menu, type MenuItem } from '../components/Menu'
import { Empty, LoadFailed, Loading } from '../components/States'
import { Callout, OverduePill, cx } from '../components/ui'
import {
  AGENCY_SORTS,
  filingLabel,
  jurisdictionLabel,
  letterLines,
  medianText,
  sortAgencies,
  type AgencySort,
} from '../lib/agencies'
import { civilDayOf, formatDate, formatDateRange, formatShortDate } from '../lib/dates'
import { statusWords } from '../lib/status'
import { daysFigure } from '../lib/vocab'
import {
  addAgency,
  addContact,
  editAgency,
  editContact,
  editFiling,
  mergeAgencies,
  unmergeAgency,
} from './agencies/dialogs'
import { LOGIN_PATH, PortalLoginRow, editLogin } from './agencies/PortalLogin'

type AgencyRow = Schemas['AgencyRowOut']

const METHODS: Array<{ value: string; label: string }> = [
  { value: 'email', label: 'Email' },
  { value: 'portal', label: 'Portal' },
  { value: 'mail', label: 'Postal mail' },
  { value: 'none', label: 'Needs a filing method' },
]
const COLUMNS = 'minmax(0,1.6fr) minmax(0,1fr) minmax(0,1.1fr) 110px 130px'
const REQUEST_COLUMNS = '150px minmax(0,1fr) 190px 70px'

/** Every agency query the screens keep, so a change shows everywhere at once. */
function useRefreshAgencies() {
  const client = useQueryClient()
  return () =>
    Promise.all([
      client.invalidateQueries({ queryKey: ['get', '/api/records/agencies'] }),
      client.invalidateQueries({ queryKey: ['get', '/api/records/agencies/{agency_id}'] }),
      client.invalidateQueries({ queryKey: ['get', '/api/records/agencies/{agency_id}/contacts'] }),
      client.invalidateQueries({ queryKey: ['get', LOGIN_PATH] }),
    ])
}

/** A toolbar menu's face (163 "Any jurisdiction ▾"): 32 px, 12.5 px ink-2, a 9 px chevron. */
function MenuFace({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-8 px-2.5 text-125 whitespace-nowrap text-ink-2 hover:bg-hover">
      {children}
      <ChevronDown size={9} width={2.6} stroke="var(--color-muted)" />
    </span>
  )
}

/** The title row's ⋯ (nothing on 163 or 164 draws these doors; they are kept here). */
function MoreMenu({ label, items }: { label: string; items: MenuItem[] }) {
  if (items.length === 0) return null
  return (
    <Menu label={label} items={items} width={220}>
      {(open) => (
        <span
          className={cx(
            'box-border inline-flex size-8 items-center justify-center rounded-9 hover:bg-hover',
            open && 'bg-hover',
          )}
        >
          <MoreIcon size={16} stroke="var(--color-ink-3)" />
        </span>
      )}
    </Menu>
  )
}

/** Agencies (163): who requests are filed with and how, with how many are open and how long
 *  each takes to answer. Search, the jurisdiction and filing filters and the order live in the
 *  address. */
export function Agencies() {
  const me = useMe()
  const navigate = useNavigate()
  const refresh = useRefreshAgencies()
  const [params, setParams] = useSearchParams()
  const [spec, setSpec] = useState<ActionSpec | null>(null)
  const q = (params.get('q') ?? '').toLowerCase()
  const jurisdiction = params.get('jurisdiction') ?? ''
  const method = params.get('method') ?? ''
  const sort = (
    AGENCY_SORTS.some((s) => s.value === params.get('sort')) ? params.get('sort') : 'requests'
  ) as AgencySort
  const showMerged = params.get('merged') === '1'
  const agencies = $api.useQuery(
    'get',
    '/api/records/agencies',
    showMerged ? { params: { query: { include_merged: true } } } : {},
  )
  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next, { replace: true })
  }
  const all = agencies.data ?? []
  const jurisdictions = [...new Set(all.map((a) => a.jurisdiction))].sort()
  const rows = sortAgencies(
    all.filter(
      (a) =>
        (!q || [a.name, a.short_name ?? '', a.jurisdiction].join(' ').toLowerCase().includes(q)) &&
        (!method || (method === 'none' ? !a.filing_method : a.filing_method === method)) &&
        (!jurisdiction || a.jurisdiction === jurisdiction),
    ),
    sort,
  )
  const more: MenuItem[] = [
    { label: 'Import agencies…', onPick: () => void navigate('/agencies/import') },
    ...(isAdmin(me)
      ? [{ label: 'Merge two agencies…', onPick: () => setSpec(mergeAgencies(all)) }]
      : []),
    {
      label: showMerged ? 'Hide merged agencies' : 'Show merged agencies',
      separator: true,
      onPick: () => set('merged', showMerged ? '' : '1'),
    },
  ]

  return (
    <div className="flex flex-col leading-[normal]">
      <PageHeader
        title="Agencies"
        actions={
          <>
            <MoreMenu label="More on agencies" items={more} />
            <button
              type="button"
              onClick={() =>
                setSpec(addAgency(all, (made) => void navigate(`/agencies/${made.id}`)))
              }
              className="box-border inline-flex h-8 shrink-0 items-center rounded-pill bg-brand px-[13px] text-125 font-medium whitespace-nowrap text-white hover:bg-brand-dark"
            >
              ＋ Add agency
            </button>
          </>
        }
      />
      <div className="flex min-w-0 flex-col gap-3.5 px-2 pb-2">
        <div className="flex items-center gap-2">
          <label className="box-border flex h-[34px] min-w-[220px] flex-1 items-center gap-[9px] border-b-[1.5px] border-line-underline pr-1">
            <SearchIcon size={15} width={2} stroke="var(--color-muted)" />
            <input
              type="search"
              aria-label="Search agencies"
              placeholder="Search agencies"
              defaultValue={params.get('q') ?? ''}
              onChange={(event) => set('q', event.target.value.trim())}
              className="min-w-0 flex-1 bg-transparent text-13 text-ink outline-none placeholder:text-muted-2 [&::-webkit-search-cancel-button]:hidden"
            />
          </label>
          <Menu
            label="Jurisdiction"
            width={220}
            panelClassName="max-h-[320px] overflow-y-auto"
            items={[
              {
                label: 'Any jurisdiction',
                current: !jurisdiction,
                onPick: () => set('jurisdiction', ''),
              },
              ...jurisdictions.map((value) => ({
                label: value,
                current: value === jurisdiction,
                onPick: () => set('jurisdiction', value),
              })),
            ]}
          >
            <MenuFace>{jurisdiction || 'Any jurisdiction'}</MenuFace>
          </Menu>
          <Menu
            label="Filing method"
            width={220}
            items={[
              { label: 'Any filing method', current: !method, onPick: () => set('method', '') },
              ...METHODS.map((m) => ({
                label: m.label,
                current: m.value === method,
                onPick: () => set('method', m.value),
              })),
            ]}
          >
            <MenuFace>
              {METHODS.find((m) => m.value === method)?.label ?? 'Any filing method'}
            </MenuFace>
          </Menu>
        </div>
        <div className="-mt-0.5 -mb-1 flex h-7 items-center gap-2.5 pr-2 pl-[13px]">
          <span className="text-125 text-muted">
            {agencies.data ? `${rows.length} ${rows.length === 1 ? 'agency' : 'agencies'}` : ''}
          </span>
          <Menu
            label="Order"
            width={200}
            className="ml-auto"
            items={AGENCY_SORTS.map((s) => ({
              label: s.label,
              current: s.value === sort,
              onPick: () => set('sort', s.value === 'requests' ? '' : s.value),
            }))}
          >
            <span className="inline-flex items-center gap-1.5 text-125 text-ink-2">
              {AGENCY_SORTS.find((s) => s.value === sort)?.label}
              <ChevronDown size={9} width={2.6} stroke="var(--color-muted)" />
            </span>
          </Menu>
        </div>
        {agencies.isPending ? (
          <Loading what="Loading agencies" />
        ) : agencies.isError ? (
          <LoadFailed
            what="the agencies"
            error={agencies.error}
            onRetry={() => void agencies.refetch()}
          />
        ) : rows.length === 0 ? (
          all.length === 0 ? (
            <Empty title="No agencies yet">Add the first agency to file a request with it.</Empty>
          ) : (
            <Empty title="No agencies match">Try a different search or filter.</Empty>
          )
        ) : (
          <ListCard aria-label="Agencies">
            <ListHead columns={COLUMNS}>
              <span>Agency</span>
              <span>Jurisdiction</span>
              <span>Files by</span>
              <span className="text-right">Open</span>
              <span className="text-right">Median response</span>
            </ListHead>
            {rows.map((agency, index) => (
              <AgencyLine key={agency.id} agency={agency} index={index} />
            ))}
          </ListCard>
        )}
      </div>
      <ActionDialog
        spec={spec}
        onClose={() => setSpec(null)}
        onDone={() => {
          setSpec(null)
          void refresh()
        }}
      />
    </div>
  )
}

function AgencyLine({ agency, index }: { agency: AgencyRow; index: number }) {
  const filing = filingLabel(agency)
  return (
    <ListRow columns={COLUMNS} index={index} to={`/agencies/${agency.id}`} pad="py-3">
      <span className="min-w-0 truncate">
        {agency.name}
        {agency.merged_into_agency_id ? <span className="text-muted"> · merged</span> : null}
      </span>
      <span className="min-w-0 truncate text-ink-2">{agency.jurisdiction}</span>
      <span className={cx('min-w-0 truncate', filing.missing ? 'text-warn' : 'text-ink-2')}>
        {filing.text}
      </span>
      <span className="text-right text-ink-2">{agency.open_requests}</span>
      <span className="text-right text-ink-2">
        {medianText(agency.median_days_to_determination)}
      </span>
    </ListRow>
  )
}

/** The right column's outlined cards (164 Contacts, Response record): grey ground, 12 px. */
function SideCard({ children }: { children: ReactNode }) {
  return (
    <section className="overflow-hidden rounded-12 border border-line-chip bg-surface-row">
      {children}
    </section>
  )
}

/** The agency page (164): Filing and its requests at the left; contacts, the response record,
 *  and (kept from before) the names merged into it and its closure days at the right. */
export function AgencyPage() {
  const { agencyId = '' } = useParams()
  const me = useMe()
  const refresh = useRefreshAgencies()
  const [spec, setSpec] = useState<ActionSpec | null>(null)
  const path = { params: { path: { agency_id: agencyId } } }
  const detail = $api.useQuery('get', '/api/records/agencies/{agency_id}', path)
  const contacts = $api.useQuery('get', '/api/records/agencies/{agency_id}/contacts', path)
  const login = $api.useQuery('get', LOGIN_PATH, path)
  const directory = $api.useQuery('get', '/api/records/agencies', {}, { staleTime: 60_000 })
  if (detail.isPending) return <Loading what="Loading the agency" />
  if (detail.isError)
    return (
      <LoadFailed what="this agency" error={detail.error} onRetry={() => void detail.refetch()} />
    )
  const agency = detail.data
  const record = agency.response_record
  const filing = filingLabel(agency)
  const letter = letterLines(agency)
  const verified = agency.request_email_verified_at
    ? formatShortDate(civilDayOf(agency.request_email_verified_at, me.timezone))
    : null
  const stored = login.data ?? null
  const more: MenuItem[] = [
    {
      label: 'Edit agency…',
      onPick: () => setSpec(editAgency(agency, directory.data ?? [agency])),
    },
    ...(stored === null && !login.isPending
      ? [{ label: 'Add a portal login…', onPick: () => setSpec(editLogin(agency.id, null)) }]
      : []),
    ...(isAdmin(me) && agency.merged_into_agency_id
      ? [{ label: 'Unmerge…', onPick: () => setSpec(unmergeAgency(agency)) }]
      : []),
  ]

  return (
    <div className="flex flex-col leading-[normal]">
      <PageHeader
        title={agency.name}
        subtitle={jurisdictionLabel(agency.jurisdiction, agency.records_law)}
        actions={<MoreMenu label="More on this agency" items={more} />}
      />
      <div className="flex min-w-0 flex-col gap-3.5 px-2 pb-2">
        {agency.merged_into_agency_id ? (
          <Callout tone="amber">
            Merged into another agency on {formatDate(agency.merged_at?.slice(0, 10))}.{' '}
            <Link
              className="font-medium underline"
              to={`/agencies/${agency.merged_into_agency_id}`}
            >
              Open it
            </Link>
          </Callout>
        ) : null}
        <div className="flex items-start gap-[18px]">
          <div className="flex min-w-0 flex-1 flex-col gap-3.5">
            <ListCard aria-label="Filing">
              <CardHead
                title="Filing"
                action={<CardAction onClick={() => setSpec(editFiling(agency))}>Edit</CardAction>}
              />
              <InfoRow label="Files by">
                <span className={cx('min-w-0', filing.missing ? 'text-warn' : 'text-ink')}>
                  {filing.text}
                </span>
              </InfoRow>
              {agency.default_request_email ? (
                <InfoRow label="Address">
                  <span className="min-w-0 break-words text-ink">
                    {agency.default_request_email}
                    {verified ? ` · verified ${verified}` : ''}
                  </span>
                </InfoRow>
              ) : null}
              {agency.portal_url ? (
                <InfoRow label="Form address">
                  <span className="min-w-0 break-all text-ink">{agency.portal_url}</span>
                </InfoRow>
              ) : null}
              {letter.length > 0 ? (
                <InfoRow label="Letter goes to">
                  <span className="min-w-0 text-ink">
                    {letter.map((line, i) => (
                      <span key={i}>
                        {i > 0 ? <br /> : null}
                        {line}
                      </span>
                    ))}
                  </span>
                </InfoRow>
              ) : null}
              {agency.short_name ? (
                <InfoRow label="Abbreviation">
                  <span className="min-w-0 text-ink">{agency.short_name}</span>
                </InfoRow>
              ) : null}
              {stored || agency.filing_method === 'portal' ? (
                <PortalLoginRow
                  key={agency.id}
                  agencyId={agency.id}
                  stored={stored}
                  onAction={setSpec}
                />
              ) : null}
              {agency.notes ? (
                <InfoRow label="Notes">
                  <span className="min-w-0 whitespace-pre-wrap text-ink">{agency.notes}</span>
                </InfoRow>
              ) : null}
            </ListCard>

            <ListCard aria-label="Requests">
              <CardHead title="Requests" />
              <ListHead columns={REQUEST_COLUMNS} ruled="both">
                <span>Tracking no.</span>
                <span>Subject</span>
                <span>Status</span>
                <span className="text-right">Days</span>
              </ListHead>
              {agency.requests.length === 0 ? (
                <p className="px-9 py-[11px] text-125 text-muted">
                  No requests you can see are filed here.
                </p>
              ) : (
                agency.requests.map((row, index) => {
                  const words = statusWords(row.current_status)
                  const days = daysFigure(row.days_to_next_deadline)
                  return (
                    <ListRow
                      key={row.id}
                      columns={REQUEST_COLUMNS}
                      index={index}
                      to={`/requests/${row.id}`}
                    >
                      <span className="text-ink-2">{row.tracking_number}</span>
                      <span className="min-w-0 truncate">{row.subject}</span>
                      <span className="min-w-0 truncate">
                        {words.qualifier ? (
                          <>
                            <span className="text-muted">{words.family} · </span>
                            <span className="font-medium">{words.qualifier}</span>
                          </>
                        ) : (
                          <span className="font-medium">{words.family}</span>
                        )}
                      </span>
                      <span className="text-right">
                        {days.overdue ? (
                          <OverduePill>{days.text}</OverduePill>
                        ) : (
                          <span className="text-ink-2">{days.text}</span>
                        )}
                      </span>
                    </ListRow>
                  )
                })
              )}
            </ListCard>
          </div>

          <div className="flex w-[340px] shrink-0 flex-col gap-3.5">
            <SideCard>
              <CardHead
                title="Contacts"
                action={<CardAction onClick={() => setSpec(addContact(agency))}>Add</CardAction>}
              />
              {(contacts.data ?? []).length === 0 ? (
                <p className="border-t border-line-row px-5 py-[11px] text-125 text-muted">
                  {contacts.isPending ? 'Loading…' : 'No contacts yet.'}
                </p>
              ) : (
                contacts.data!.map((contact) => {
                  const about = [
                    contact.title,
                    contact.is_primary ? 'primary' : null,
                    contact.copy_on_every_request ? 'copied on every request' : null,
                  ].filter(Boolean)
                  return (
                    <button
                      key={contact.id}
                      type="button"
                      onClick={() => setSpec(editContact(contact, agency))}
                      className="flex w-full items-center gap-3 border-t border-line-row px-5 py-[11px] text-left text-125 leading-[normal] hover:bg-hover"
                    >
                      <span className="min-w-0 flex-1 truncate text-ink">
                        {contact.name}
                        {about.length ? (
                          <span className="text-muted"> · {about.join(' · ')}</span>
                        ) : null}
                      </span>
                      {contact.email ? (
                        <span
                          title={contact.bad_address_at ? 'This address bounced' : undefined}
                          className={cx(
                            'max-w-[55%] min-w-0 truncate',
                            contact.bad_address_at ? 'text-danger line-through' : 'text-ink-2',
                          )}
                        >
                          {contact.email}
                        </span>
                      ) : null}
                    </button>
                  )
                })
              )}
            </SideCard>

            <SideCard>
              <CardHead title="Response record" />
              <InfoRow label="Median response">
                <span className="min-w-0 text-ink">
                  {medianText(record.median_days_to_determination)}
                </span>
              </InfoRow>
              <InfoRow label="On time">
                <span className="min-w-0 text-ink">
                  {record.determinations_total
                    ? `${record.determinations_on_time} of ${record.determinations_total} determinations`
                    : '—'}
                </span>
              </InfoRow>
              <InfoRow label="Extensions taken">
                <span className="min-w-0 text-ink">{record.extensions_taken}</span>
              </InfoRow>
            </SideCard>

            {agency.merged_from.length > 0 ? (
              <SideCard>
                <CardHead title="Also known as" />
                {agency.merged_from.map((other) => (
                  <Link
                    key={other.id}
                    to={`/agencies/${other.id}`}
                    className="flex gap-3 border-t border-line-row px-5 py-[11px] text-125 text-ink hover:bg-hover hover:text-ink"
                  >
                    <span className="min-w-0 flex-1 truncate">{other.name}</span>
                    <span className="text-muted">
                      merged {formatShortDate(other.merged_at?.slice(0, 10))}
                    </span>
                  </Link>
                ))}
              </SideCard>
            ) : null}

            {agency.closures.length > 0 ? (
              <SideCard>
                <CardHead title="Closure days" />
                {agency.closures.map((closure) => (
                  <div
                    key={closure.id}
                    className="border-t border-line-row px-5 py-[11px] text-125 leading-[normal]"
                  >
                    <span className={cx('block', closure.voided_at && 'text-muted line-through')}>
                      {formatDateRange(closure.starts_on, closure.ends_on)} · {closure.reason}
                    </span>
                    <span className="mt-0.5 block text-115 text-muted">
                      {closure.voided_at ? `Voided. ${closure.void_note ?? ''}` : closure.citation}
                    </span>
                  </div>
                ))}
              </SideCard>
            ) : null}
          </div>
        </div>
      </div>
      <ActionDialog
        spec={spec}
        onClose={() => setSpec(null)}
        onDone={() => {
          setSpec(null)
          void refresh()
        }}
      />
    </div>
  )
}
