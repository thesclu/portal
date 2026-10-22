/** Plain figures for the Reports screen and the CSV links (P15c). Nothing here judges a number. */

/** `12` -> `12 days`, `1` -> `1 day`, nothing -> `—`. */
export function dayCount(days: number | null | undefined): string {
  if (days === null || days === undefined) return '—'
  return `${days} ${days === 1 ? 'day' : 'days'}`
}

/** `5 of 6` -> `83%`; nothing to judge -> `—`. */
export function share(part: number, whole: number): string {
  if (whole <= 0) return '—'
  return `${Math.round((part / whole) * 100)}%`
}

/** A bar's width as a percentage of the longest bar, 0 when there is nothing to draw. */
export function barWidth(value: number | null | undefined, max: number): number {
  if (value === null || value === undefined || max <= 0) return 0
  return Math.max(0, Math.min(100, (value / max) * 100))
}

/** A CSV download link for the same filters a list uses: blank filters are left out, so the
 *  address stays short and the session cookie goes with the click. */
export function csvHref(
  path: string,
  query: Record<string, string | number | null | undefined>,
): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') continue
    params.set(key, String(value))
  }
  const search = params.toString()
  return search ? `${path}?${search}` : path
}
