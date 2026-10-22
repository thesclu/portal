/**
 * The only two ways a date reaches the screen.
 *
 * An INSTANT (when something happened) is shown in the reader's own time zone with the zone
 * named, because "3:04 PM" with no zone is a different moment to two people. A CIVIL DATE (a
 * deadline, a letter's date) has no time and no zone: 2026-09-18 is the 18th wherever you are,
 * so it must never pass through a Date in local time, which would move it by a day west of UTC.
 */

const instantFormats = new Map<string, Intl.DateTimeFormat>()

function instantFormat(timeZone: string): Intl.DateTimeFormat {
  let format = instantFormats.get(timeZone)
  if (!format) {
    format = new Intl.DateTimeFormat('en-US', {
      timeZone,
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      timeZoneName: 'short',
    })
    instantFormats.set(timeZone, format)
  }
  return format
}

/** `2026-09-08T22:04:00Z` in `America/Los_Angeles` -> `Sep 8, 2026, 3:04 PM PDT`. */
export function formatInstant(iso: string | null | undefined, timeZone: string): string {
  if (!iso) return '—'
  const when = new Date(iso)
  if (Number.isNaN(when.getTime())) return '—'
  try {
    return instantFormat(timeZone).format(when)
  } catch {
    // An unknown zone name must not blank the page; UTC, labelled, is still true.
    return instantFormat('UTC').format(when)
  }
}

const timeFormats = new Map<string, Intl.DateTimeFormat>()

/** `2026-09-08T22:04:00Z` in `America/Los_Angeles` -> `3:04 PM`: the clock alone, for a line
 *  such as "Draft saved 3:04 PM" where the day is today and the zone is the reader's own. */
export function formatTime(iso: string | null | undefined, timeZone: string): string {
  if (!iso) return '—'
  const when = new Date(iso)
  if (Number.isNaN(when.getTime())) return '—'
  const make = (zone: string) =>
    new Intl.DateTimeFormat('en-US', { timeZone: zone, hour: 'numeric', minute: '2-digit' })
  let format = timeFormats.get(timeZone)
  try {
    if (!format) {
      format = make(timeZone)
      timeFormats.set(timeZone, format)
    }
    return format.format(when)
  } catch {
    return make('UTC').format(when)
  }
}

const civilFormat = new Intl.DateTimeFormat('en-US', {
  timeZone: 'UTC',
  month: 'short',
  day: 'numeric',
  year: 'numeric',
})

/** `2026-09-18` -> `Sep 18, 2026`, the same for every reader. */
export function formatDate(civil: string | null | undefined): string {
  if (!civil) return '—'
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(civil)
  if (!match) return '—'
  const [, year, month, day] = match
  return civilFormat.format(new Date(Date.UTC(Number(year), Number(month) - 1, Number(day))))
}

/** `2026-09-01`..`2026-09-03` -> `Sep 1, 2026 – Sep 3, 2026`; one date when both are the same. */
export function formatDateRange(start: string, end: string): string {
  return start === end ? formatDate(start) : `${formatDate(start)} – ${formatDate(end)}`
}

/** How long ago, in words, for a line like "Last message 4 min ago". */
export function formatAgo(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return 'never'
  const seconds = Math.round((now.getTime() - new Date(iso).getTime()) / 1000)
  if (Number.isNaN(seconds)) return 'never'
  if (seconds < 60) return 'just now'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  if (hours < 48) return `${hours} hr ago`
  return `${Math.round(hours / 24)} days ago`
}

const shortFormats = new Map<string, Intl.DateTimeFormat>()

function zoned(
  cache: Map<string, Intl.DateTimeFormat>,
  key: string,
  make: (zone: string) => Intl.DateTimeFormat,
  timeZone: string,
): Intl.DateTimeFormat {
  let format = cache.get(`${key}:${timeZone}`)
  if (!format) {
    try {
      format = make(timeZone)
    } catch {
      format = make('UTC')
    }
    cache.set(`${key}:${timeZone}`, format)
  }
  return format
}

/** `2026-09-07T16:14:00Z` in `America/Los_Angeles` -> `Sep 7`: a table's or a card's date, the
 *  full instant (zone named) in its title. */
export function formatShortInstant(iso: string | null | undefined, timeZone: string): string {
  if (!iso) return '—'
  const when = new Date(iso)
  if (Number.isNaN(when.getTime())) return '—'
  return zoned(
    shortFormats,
    'md',
    (zone) => new Intl.DateTimeFormat('en-US', { timeZone: zone, month: 'short', day: 'numeric' }),
    timeZone,
  ).format(when)
}

/** `Sep 7, 9:18 AM`: a note's time (066), in the reader's zone. */
export function formatDayTime(iso: string | null | undefined, timeZone: string): string {
  if (!iso) return '—'
  const when = new Date(iso)
  if (Number.isNaN(when.getTime())) return '—'
  return zoned(
    shortFormats,
    'mdt',
    (zone) =>
      new Intl.DateTimeFormat('en-US', {
        timeZone: zone,
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
      }),
    timeZone,
  ).format(when)
}

/** The civil date an instant falls on in a zone, `YYYY-MM-DD`. */
export function civilDayOf(iso: string, timeZone: string): string {
  return zoned(
    shortFormats,
    'iso',
    (zone) =>
      new Intl.DateTimeFormat('en-CA', {
        timeZone: zone,
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
      }),
    timeZone,
  ).format(new Date(iso))
}

const civilShort = new Intl.DateTimeFormat('en-US', {
  timeZone: 'UTC',
  month: 'short',
  day: 'numeric',
})

/** `2026-09-18` -> `Sep 18`, the same for every reader. */
export function formatShortDate(civil: string | null | undefined): string {
  if (!civil) return '—'
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(civil)
  if (!match) return '—'
  const [, year, month, day] = match
  return civilShort.format(new Date(Date.UTC(Number(year), Number(month) - 1, Number(day))))
}

/** An Inbox row's time (106): the clock today ("6:20 AM"), "Yesterday", else the short date
 *  ("Sep 6"), each in the reader's zone; the full instant goes in the row's title. */
export function formatListTime(
  iso: string | null | undefined,
  timeZone: string,
  now: Date = new Date(),
): string {
  if (!iso) return '—'
  const when = new Date(iso)
  if (Number.isNaN(when.getTime())) return '—'
  const day = civilDayOf(iso, timeZone)
  const today = civilDayOf(now.toISOString(), timeZone)
  if (day === today) return formatTime(iso, timeZone)
  const [y, m, d] = today.split('-').map(Number)
  const yesterday = new Date(Date.UTC(y, m - 1, d - 1)).toISOString().slice(0, 10)
  if (day === yesterday) return 'Yesterday'
  return formatShortInstant(iso, timeZone)
}

/** When a person was last about (205's Last active): "Now" within two minutes, "12 min ago",
 *  "3 hr ago" the same day, "Yesterday", else the short date (with the year when it differs). */
export function formatLastActive(
  iso: string | null | undefined,
  timeZone: string,
  now: Date = new Date(),
): string {
  if (!iso) return 'Never'
  const when = new Date(iso)
  if (Number.isNaN(when.getTime())) return 'Never'
  const minutes = Math.floor((now.getTime() - when.getTime()) / 60_000)
  if (minutes < 2) return 'Now'
  if (minutes < 60) return `${minutes} min ago`
  const day = civilDayOf(iso, timeZone)
  const today = civilDayOf(now.toISOString(), timeZone)
  if (day === today) return `${Math.floor(minutes / 60)} hr ago`
  const [y, m, d] = today.split('-').map(Number)
  const yesterday = new Date(Date.UTC(y, m - 1, d - 1)).toISOString().slice(0, 10)
  if (day === yesterday) return 'Yesterday'
  return day.slice(0, 4) === today.slice(0, 4) ? formatShortDate(day) : formatDate(day)
}

/** When something was submitted (186's Submitted): "Today 8:40 AM", "Yesterday", else the short
 *  date, in the reader's zone. */
export function formatSubmitted(
  iso: string | null | undefined,
  timeZone: string,
  now: Date = new Date(),
): string {
  if (!iso) return '—'
  const when = new Date(iso)
  if (Number.isNaN(when.getTime())) return '—'
  const day = civilDayOf(iso, timeZone)
  const today = civilDayOf(now.toISOString(), timeZone)
  if (day === today) return `Today ${formatTime(iso, timeZone)}`
  const [y, m, d] = today.split('-').map(Number)
  const yesterday = new Date(Date.UTC(y, m - 1, d - 1)).toISOString().slice(0, 10)
  if (day === yesterday) return 'Yesterday'
  return day.slice(0, 4) === today.slice(0, 4) ? formatShortDate(day) : formatDate(day)
}
