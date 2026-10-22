/** Who is where: the words the top bar's "N online" control and the sidebar's own card use. */

/** The shape of one seen person this module needs; the API's SeenOut has more. */
export interface Seen {
  user_id: string
  name: string
  path: string
  state: string
}

/** "Dana Nolan" → "DN"; one word gives one letter; nothing gives nothing. */
export function initialsOf(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean)
  if (words.length === 0) return ''
  const first = words[0]!
  const last = words[words.length - 1]!
  const letters = words.length === 1 ? first.slice(0, 1) : first.slice(0, 1) + last.slice(0, 1)
  return letters.toUpperCase()
}

/** The short name of a time zone at a moment: "PDT", "PST", "UTC", "GMT+2". An unknown zone
 *  gives nothing rather than throwing. */
export function zoneAbbreviation(timeZone: string, at: Date = new Date()): string {
  try {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone,
      timeZoneName: 'short',
    }).formatToParts(at)
    return parts.find((part) => part.type === 'timeZoneName')?.value ?? ''
  } catch {
    return ''
  }
}

/** Where a page says it is, for the presence list: its trail and then its name, joined by ›. */
export function pageLabel(title: string, trail?: readonly string[]): string {
  return [...(trail ?? []), title].filter((part) => part.trim() !== '').join(' › ')
}

/** The people on one request's page, other than the reader. A path is on the page when it is
 *  `/requests/<id>` itself or that followed by a slash or a query. */
export function viewersOf<T extends Seen>(
  people: readonly T[],
  requestId: string,
  meId: string,
): T[] {
  const base = `/requests/${requestId}`
  return people.filter(
    (person) =>
      person.user_id !== meId &&
      (person.path === base ||
        person.path.startsWith(`${base}/`) ||
        person.path.startsWith(`${base}?`)),
  )
}
