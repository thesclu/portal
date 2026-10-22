/**
 * How an agency reads on the Agencies screens (163–172): how it files, where its letter goes,
 * the list's order, and the filing choices a dialog offers. Pure, so it is tested.
 */

export interface AgencyLike {
  name: string
  jurisdiction: string
  records_law?: string | null
  filing_method?: string | null
  portal_kind?: string | null
  portal_automation?: string | null
  recipient_office?: string | null
  mailing_street?: string | null
  mailing_city_state_zip?: string | null
  open_requests?: number
  requests_total?: number
  median_days_to_determination?: number | null
}

/** "Email", "GovQA · automatic", "Web form", "Postal mail"; with none, the warning (163). */
export function filingLabel(agency: AgencyLike): { text: string; missing: boolean } {
  switch (agency.filing_method) {
    case 'email':
      return { text: 'Email', missing: false }
    case 'mail':
      return { text: 'Postal mail', missing: false }
    case 'portal': {
      const kind = agency.portal_kind || 'Portal'
      const automatic = agency.portal_automation && agency.portal_automation !== 'none'
      return { text: automatic ? `${kind} · automatic` : kind, missing: false }
    }
    default:
      return { text: 'Needs a filing method', missing: true }
  }
}

/**
 * The one "Files by" choice the dialogs draw (165, 166) for the directory's two fields: Email,
 * a kind of portal, a portal of no named kind, or post. Each value is `method` or
 * `method:kind`.
 */
export const FILING_CHOICES: Array<{ value: string; label: string }> = [
  { value: 'email', label: 'Email' },
  { value: 'portal:Web form', label: 'Web form' },
  { value: 'portal:GovQA', label: 'GovQA' },
  { value: 'portal:NextRequest', label: 'NextRequest' },
  { value: 'mail', label: 'Postal mail' },
]

export function filingChoice(agency: Pick<AgencyLike, 'filing_method' | 'portal_kind'>): string {
  if (agency.filing_method === 'portal') {
    return agency.portal_kind ? `portal:${agency.portal_kind}` : 'portal'
  }
  return agency.filing_method ?? ''
}

export function fromFilingChoice(choice: string): {
  filing_method: string | null
  portal_kind: string | null
} {
  if (!choice) return { filing_method: null, portal_kind: null }
  const [method, kind] = choice.split(':')
  return { filing_method: method, portal_kind: method === 'portal' ? (kind ?? null) : null }
}

/** The lines of "Letter goes to" (164): the office, the street, the city. */
export function letterLines(agency: AgencyLike): string[] {
  return [agency.recipient_office, agency.mailing_street, agency.mailing_city_state_zip]
    .map((line) => (line ?? '').trim())
    .filter(Boolean)
}

/**
 * "Letter goes to" written as lines (165, 170) back into the three fields: one line is the
 * office; two are the street and the city; three or more are the office, the street (the
 * middle lines joined) and the city.
 */
export function parseLetter(text: string): {
  recipient_office: string | null
  mailing_street: string | null
  mailing_city_state_zip: string | null
} {
  const lines = text
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
  if (lines.length === 0) {
    return { recipient_office: null, mailing_street: null, mailing_city_state_zip: null }
  }
  if (lines.length === 1) {
    return { recipient_office: lines[0], mailing_street: null, mailing_city_state_zip: null }
  }
  if (lines.length === 2) {
    return { recipient_office: null, mailing_street: lines[0], mailing_city_state_zip: lines[1] }
  }
  return {
    recipient_office: lines[0],
    mailing_street: lines.slice(1, -1).join(', '),
    mailing_city_state_zip: lines.at(-1) ?? null,
  }
}

/** "California · CPRA": a jurisdiction and its records law, as the add dialog offers them. */
export function jurisdictionLabel(jurisdiction: string, law?: string | null): string {
  return law ? `${jurisdiction} · ${law}` : jurisdiction
}

/** The jurisdictions (with their laws) the directory already uses, each once, by name. */
export function knownJurisdictions(
  agencies: AgencyLike[],
): Array<{ jurisdiction: string; law: string | null; label: string }> {
  const seen = new Map<string, { jurisdiction: string; law: string | null; label: string }>()
  for (const agency of agencies) {
    const law = agency.records_law || null
    const label = jurisdictionLabel(agency.jurisdiction, law)
    if (!seen.has(label)) seen.set(label, { jurisdiction: agency.jurisdiction, law, label })
  }
  return [...seen.values()].sort((a, b) => a.label.localeCompare(b.label))
}

export const AGENCY_SORTS = [
  { value: 'requests', label: 'Most requests' },
  { value: 'open', label: 'Most open' },
  { value: 'slowest', label: 'Slowest to answer' },
  { value: 'name', label: 'Name' },
] as const
export type AgencySort = (typeof AGENCY_SORTS)[number]['value']

/** The list's order (163 "Most requests"): ties, and the slowest order's agencies with no
 *  answer yet, fall back to the name. */
export function sortAgencies<T extends AgencyLike>(agencies: T[], sort: AgencySort): T[] {
  const byName = (a: T, b: T) => a.name.localeCompare(b.name)
  const by = (figure: (a: T) => number) => (a: T, b: T) => figure(b) - figure(a) || byName(a, b)
  switch (sort) {
    case 'name':
      return agencies.toSorted(byName)
    case 'open':
      return agencies.toSorted(by((a) => a.open_requests ?? 0))
    case 'slowest':
      return agencies.toSorted(by((a) => a.median_days_to_determination ?? -1))
    default:
      return agencies.toSorted(by((a) => a.requests_total ?? 0))
  }
}

/** "11 days", "1 day", "—" (163's Median response). */
export function medianText(days: number | null | undefined): string {
  if (days === null || days === undefined) return '—'
  return `${days} ${days === 1 ? 'day' : 'days'}`
}
