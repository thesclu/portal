import { useSearchParams } from 'react-router'

import { type Schemas } from '../api/client'
import { $api } from '../api/query'
import { PageHeader } from '../app/layout'
import { ChevronDown } from '../components/icons'
import { CardHead, ListCard, ListHead } from '../components/ListTable'
import { Menu } from '../components/Menu'
import { Empty, LoadFailed, Loading } from '../components/States'
import { barWidth, csvHref, dayCount, share } from '../lib/reports'
import { money } from '../lib/vocab'

type Line = Schemas['ReportAgencyLine']

/** The year shown before the report says which years exist. */
const THIS_YEAR = new Date().getFullYear()
const OUTCOMES = 'minmax(0,1.4fr) 90px 90px 90px 90px 90px'

/** A filter's face (042 "2026 ▾"): 32 px, 12.5 px ink-2, a 10 px chevron. */
function Face({ children }: { children: string }) {
  return (
    <span className="inline-flex h-8 items-center gap-1.5 px-2.5 text-125 text-ink-2">
      {children}
      <ChevronDown size={10} width={2.4} stroke="var(--color-ink-3)" />
    </span>
  )
}

/**
 * Reports (042): one year's figures over what the reader may see, for every agency or one, and
 * the same as a CSV. The year and agency live in the address so a view can be sent on. Not drawn:
 * the campaign filter (campaigns were dropped) and the dashed statute line on the bars, which
 * would be a legal number in the interface (DECISIONS 20); every bar is drawn in the brand.
 */
export function Reports() {
  const [params, setParams] = useSearchParams()
  const yearParam = Number(params.get('year')) || undefined
  const agency = params.get('agency') ?? ''
  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next, { replace: true })
  }

  const agencies = $api.useQuery('get', '/api/records/agencies', {}, { staleTime: 60_000 })
  const report = $api.useQuery(
    'get',
    '/api/records/reports',
    { params: { query: { year: yearParam, agency_id: agency || undefined } } },
    { placeholderData: (previous) => previous },
  )
  const data = report.data
  const year = yearParam ?? data?.year ?? THIS_YEAR
  const years = data?.years ?? []
  const yearOptions = years.includes(year) ? years : [year, ...years]
  const agencyName = (agencies.data ?? []).find((a) => a.id === agency)?.name

  return (
    <div className="flex flex-col leading-[normal]">
      <PageHeader title="Reports" />
      <div className="flex min-w-0 flex-col gap-3.5 px-2 pb-2">
        <div className="flex items-center gap-2">
          <Menu
            label="Year"
            align="left"
            width={140}
            items={yearOptions.map((option) => ({
              label: String(option),
              current: option === year,
              onPick: () => set('year', String(option)),
            }))}
          >
            <Face>{String(year)}</Face>
          </Menu>
          <Menu
            label="Agency"
            align="left"
            width={240}
            panelClassName="max-h-[320px] overflow-y-auto"
            items={[
              { label: 'All agencies', current: !agency, onPick: () => set('agency', '') },
              ...(agencies.data ?? []).map((a) => ({
                label: a.name,
                current: a.id === agency,
                onPick: () => set('agency', a.id),
              })),
            ]}
          >
            <Face>{agencyName ?? 'All agencies'}</Face>
          </Menu>
          <span className="ml-auto">
            <a
              href={csvHref('/api/records/reports/export', { year, agency_id: agency })}
              download
              className="box-border inline-flex h-8 items-center gap-1.5 rounded-9 px-3.5 text-125 font-medium whitespace-nowrap text-ink-2 hover:bg-hover hover:text-ink-2"
            >
              Export CSV
            </a>
          </span>
        </div>

        {report.isPending ? (
          <Loading what="Loading the report" />
        ) : report.isError ? (
          <LoadFailed
            what="the report"
            error={report.error}
            onRetry={() => void report.refetch()}
          />
        ) : !data || data.filed === 0 ? (
          <Empty title={`Nothing filed in ${year}.`} />
        ) : (
          <div
            className={
              report.isFetching ? 'flex flex-col gap-3.5 opacity-60' : 'flex flex-col gap-3.5'
            }
          >
            <div className="flex gap-3.5">
              <Tile
                value={dayCount(data.median_days_to_determination)}
                label="Median time to determination"
              />
              <Tile value={share(data.on_time, data.judged)} label="Determinations on time" />
              <Tile value={String(data.overdue_now)} label="Overdue now" />
              <Tile value={money(data.fees_paid_cents)} label="Fees paid this year" />
            </div>

            <ListCard aria-label="Median days to determination">
              <CardHead title="Median days to determination" />
              <MedianBars lines={data.agencies} />
              <div className="h-2.5" />
            </ListCard>

            <ListCard aria-label="Outcomes">
              <CardHead title="Outcomes" count={`by request, ${year}`} />
              <ListHead columns={OUTCOMES} ruled="both">
                <span>Agency</span>
                <span className="text-right">Filed</span>
                <span className="text-right">Produced</span>
                <span className="text-right">Partial</span>
                <span className="text-right">Denied</span>
                <span className="text-right">No records</span>
              </ListHead>
              {data.agencies.map((line) => (
                <div
                  key={line.agency_id}
                  role="row"
                  style={{ gridTemplateColumns: OUTCOMES }}
                  className="grid items-center gap-x-3.5 border-b border-transparent px-9 py-[11px] text-125 text-ink last:border-b-0"
                >
                  <span className="min-w-0 truncate">{line.agency_name}</span>
                  <span className="text-right">{line.filed}</span>
                  <span className="text-right">{line.produced}</span>
                  <span className="text-right">{line.partial}</span>
                  <span className="text-right">{line.denied}</span>
                  <span className="text-right">{line.no_records}</span>
                </div>
              ))}
            </ListCard>
          </div>
        )}
      </div>
    </div>
  )
}

function Tile({ value, label }: { value: string; label: string }) {
  return (
    <section className="flex-1 rounded-16 bg-card px-[18px] py-3.5 shadow-float">
      <div className="text-24 font-semibold text-ink">{value}</div>
      <div className="mt-0.5 text-12 text-muted">{label}</div>
    </section>
  )
}

/** One bar per agency, quickest first; an agency with no determination yet is listed last,
 *  without a bar. */
function MedianBars({ lines }: { lines: Line[] }) {
  const withMedian = lines
    .filter((line) => line.median_days_to_determination !== null)
    .sort((a, b) => a.median_days_to_determination! - b.median_days_to_determination!)
  const without = lines.filter((line) => line.median_days_to_determination === null)
  const max = Math.max(0, ...withMedian.map((line) => line.median_days_to_determination!))
  if (lines.length === 0) {
    return <p className="px-5 py-[7px] text-125 text-muted">No agencies this year.</p>
  }
  return (
    <>
      {[...withMedian, ...without].map((line) => {
        const median = line.median_days_to_determination
        return (
          <div key={line.agency_id} className="flex items-center gap-3 px-5 py-[7px] text-125">
            <span className="w-[190px] shrink-0 truncate text-ink">{line.agency_name}</span>
            <span aria-hidden className="relative h-2 flex-1 rounded-4 bg-line-row">
              <span
                className="absolute inset-y-0 left-0 rounded-4 bg-brand"
                style={{ width: `${barWidth(median, max)}%` }}
              />
            </span>
            <span className="w-[52px] shrink-0 text-right whitespace-nowrap text-ink-2">
              {dayCount(median)}
            </span>
          </div>
        )
      })}
    </>
  )
}
