/**
 * Pure helpers for Settings → Filing & deadlines and Settings → Organisation: how a stored value
 * is shown and read back, the time-zone list, and which setting keys each tab places so the
 * rest can be listed under "Other".
 */

export const MEBIBYTE = 1_048_576

/** The number a person sees for a stored one: bytes become megabytes when `scale` is MEBIBYTE. */
export function displayValue(stored: number, scale = 1): number {
  return stored / scale
}

/** The number to store for the one a person typed, rounded to a whole stored unit. */
export function storedValue(shown: number, scale = 1): number {
  return Math.round(shown * scale)
}

/** Every IANA zone the browser knows, with the current one first when it is not among them. */
export function timeZones(current: string): string[] {
  const all = typeof Intl.supportedValuesOf === 'function' ? Intl.supportedValuesOf('timeZone') : []
  return all.includes(current) ? all : [current, ...all]
}

/** The keys Settings → Filing & deadlines places in its sections. */
export const FILING_KEYS = [
  'records.followup_first_delay_days',
  'records.followup_interval_days',
  'records.followups_send_enabled',
  'records.followup_subject',
  'records.followup_body',
  'records.production_review_window_days',
  'records.default_sensitivity',
  'records.due_soon_window_days',
] as const

/** The keys Settings → Organisation places in its sections. */
export const ORGANISATION_KEYS = [
  'org.name',
  'org.legal_director',
  'org.short_name',
  'org.default_display_timezone',
  'org.signature',
  'mail.undo_send_seconds',
  'core.system_mail_window_days',
  'notifications.deadline_warning_days',
  'notifications.daily_summary_hour',
  'core.presence_away_minutes',
  'core.presence_gone_minutes',
  'chat.attachment_max_bytes',
  'records.upload_max_file_bytes',
  'records.upload_max_files',
  'records.inbound_max_payload_bytes',
  'records.inbound_date_window_hours',
  ...[
    'ai.general_provider',
    'ai.general_model',
    'ai.drafting_provider',
    'ai.drafting_model',
    'ai.claude_code_command',
    'ai.claude_code_allowed_tools',
    'ai.exclude_privileged',
    'ai.keep_prompts',
    'ai.daily_run_limit',
    'ai.house_style',
  ],
] as const

/** The keys Settings → System places (P21c: the drafting tool's card). */
export const SYSTEM_KEYS = [
  'drafting.fork_enabled',
  'drafting.fork_api_url',
  'drafting.fork_web_url',
  'drafting.fork_letterhead_id',
] as const

export const PLACED: ReadonlySet<string> = new Set<string>([
  ...FILING_KEYS,
  ...ORGANISATION_KEYS,
  ...SYSTEM_KEYS,
])

/** The settings neither tab places, in the API's order, so a key the backend adds later is still
 *  reachable under "Other". */
export function unplaced<T extends { key: string }>(settings: readonly T[]): T[] {
  return settings.filter((setting) => !PLACED.has(setting.key))
}

/**
 * A time zone as Settings → Profile reads it: the zone's generic name without "Time", then its
 * abbreviation now ("Pacific · PDT", 200). A zone the browser cannot name reads as its IANA name.
 */
export function zoneLabel(zone: string, at: Date = new Date()): string {
  const part = (style: 'longGeneric' | 'short') => {
    try {
      return (
        new Intl.DateTimeFormat('en-US', { timeZone: zone, timeZoneName: style })
          .formatToParts(at)
          .find((p) => p.type === 'timeZoneName')?.value ?? ''
      )
    } catch {
      return ''
    }
  }
  const generic = part('longGeneric').replace(/ Time$/, '')
  const short = part('short')
  if (!generic || generic.startsWith('GMT')) return zone.replaceAll('_', ' ')
  return short && short !== generic ? `${generic} · ${short}` : generic
}
