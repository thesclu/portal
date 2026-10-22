import { describe, expect, it } from 'vitest'

import { exportSpan } from './ExportDialog'

describe('exportSpan (P23b, 043 "Date range")', () => {
  it('asks for nothing for all time, a start for a span, the typed dates otherwise', () => {
    expect(exportSpan('all', '', '')).toEqual({})
    expect(exportSpan('custom', '2026-03-01', '2026-03-05')).toEqual({
      since: '2026-03-01',
      until: '2026-03-05',
    })
    expect(exportSpan('custom', '', '2026-03-05')).toEqual({
      since: undefined,
      until: '2026-03-05',
    })
    const week = exportSpan('7', '', '')
    expect(week.until).toBeUndefined()
    expect(week.since).toMatch(/^\d{4}-\d{2}-\d{2}$/)
  })
})
