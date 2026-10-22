/**
 * Status wording, in one place (BUILD_PLAN P20b, DECISIONS 30j, 37): every status code read as
 * family · qualifier, nineteen statuses in six families.
 *
 * Approved by owner on 2026-10-17, with "Action needed" added for the two statuses where the
 * agency is waiting on us; P20h added Filing · waiting for approval (an intern's letter waiting for
 * an approver, DECISIONS 42c). Nothing else in the portal states a status's words; correct a line
 * here and every screen follows.
 *
 * The glyph (the 12 px ring the Docket draws before a status) is drawn in v4 for seven codes
 * (025): both overdues, partial and new production, acknowledged, in progress, determination
 * issued and appealed. The others are extrapolated from those seven by family (a quarter for
 * Filing and Appeal, a half for Determination, three quarters for Production; Action needed is
 * drawn as attention, like an overdue, but hollow).
 */

export interface StatusWords {
  family: string
  /** Null when the family says it all ("Closed"). */
  qualifier: string | null
}

export const STATUS_WORDS: Record<string, StatusWords> = {
  pending_filing: { family: 'Filing', qualifier: 'pending' },
  waiting_for_approval: { family: 'Filing', qualifier: 'waiting for approval' },
  filed: { family: 'Filing', qualifier: 'filed' },
  acknowledged: { family: 'Filing', qualifier: 'acknowledged' },
  in_progress: { family: 'Determination', qualifier: 'awaited' },
  determination_issued: { family: 'Determination', qualifier: 'issued' },
  determination_overdue: { family: 'Determination', qualifier: 'overdue' },
  denied: { family: 'Determination', qualifier: 'denied' },
  no_records_claimed: { family: 'Determination', qualifier: 'no records' },
  new_production: { family: 'Production', qualifier: 'new' },
  production_unreviewed: { family: 'Production', qualifier: 'unreviewed' },
  partial_production: { family: 'Production', qualifier: 'partial' },
  production_received: { family: 'Production', qualifier: 'received' },
  production_overdue: { family: 'Production', qualifier: 'overdue' },
  clarification_requested: { family: 'Action needed', qualifier: 'clarification' },
  fee_estimate_received: { family: 'Action needed', qualifier: 'fee estimate' },
  appealed: { family: 'Appeal', qualifier: 'filed' },
  closed: { family: 'Closed', qualifier: null },
  withdrawn: { family: 'Closed', qualifier: 'withdrawn' },
}

/** The words for a code. A code the table does not know (a status added later) reads as its
 *  own code made legible, with no qualifier, so it is visible rather than blank. */
export function statusWords(code: string): StatusWords {
  const known = STATUS_WORDS[code]
  if (known) return known
  const words = code.replaceAll('_', ' ')
  return { family: words.charAt(0).toUpperCase() + words.slice(1), qualifier: null }
}

/** "Determination · overdue", or "Closed". */
export function statusText(code: string): string {
  const { family, qualifier } = statusWords(code)
  return qualifier ? `${family} · ${qualifier}` : family
}

/** Reserved and not yet out the door: pending filing, or an intern's letter waiting for its
 *  approver (P20h). The request page, its steps and its actions treat the two alike. */
export function isPreFiling(code: string): boolean {
  return code === 'pending_filing' || code === 'waiting_for_approval'
}

export function isOverdueStatus(code: string): boolean {
  return code.endsWith('_overdue')
}

/** The ring before a status (025): how much of it is filled, and in which tone. */
export type GlyphFill = 'dot' | 'small-dot' | 'quarter' | 'half' | 'three-quarters'
export type GlyphTone = 'danger' | 'brand' | 'muted'

export function statusGlyph(code: string): { fill: GlyphFill; tone: GlyphTone } {
  if (isOverdueStatus(code)) return { fill: 'dot', tone: 'danger' }
  switch (code) {
    case 'new_production':
      return { fill: 'small-dot', tone: 'brand' }
    case 'partial_production':
    case 'production_unreviewed':
      return { fill: 'three-quarters', tone: 'brand' }
    case 'production_received':
      return { fill: 'three-quarters', tone: 'muted' }
    case 'in_progress':
    case 'determination_issued':
      return { fill: 'half', tone: 'brand' }
    case 'denied':
    case 'no_records_claimed':
      return { fill: 'half', tone: 'muted' }
    case 'pending_filing':
    case 'waiting_for_approval':
    case 'filed':
      return { fill: 'quarter', tone: 'brand' }
    case 'clarification_requested':
    case 'fee_estimate_received':
      return { fill: 'quarter', tone: 'danger' }
    default:
      // acknowledged, appealed, closed, withdrawn and anything unknown
      return { fill: 'quarter', tone: 'muted' }
  }
}

/* Which statuses the Docket's stat boxes count is decided on the server
   (`records/services/docket_summary.py`, DECISIONS 34c): the summary route returns the lists and
   the screen filters the list by them. */

/**
 * The words of the request page's status steps (062, 076, 077, 078): the milestones a request
 * passes, one word each, and the step it is on now. Approved with STATUS_WORDS (2026-10-17).
 */
export const STEP_WORDS = {
  filed: 'Filed',
  filing: 'Filing',
  acknowledged: 'Acknowledged',
  determination: 'Determination',
  denied: 'Denied',
  noRecords: 'No records',
  partialProduction: 'Partial production',
  production: 'Production',
  appealed: 'Appealed',
  closed: 'Closed',
  withdrawn: 'Withdrawn',
  /** The step on now: "Determination due", "Production due" (062, 077). */
  due: (milestone: string) => `${milestone} due`,
} as const
