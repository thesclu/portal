/** Words for the codes the API speaks, and the hooks that fetch what several screens share. */
import { $api } from '../api/query'

export function useDefinitions() {
  return $api.useQuery('get', '/api/records/definitions', {}, { staleTime: 10 * 60_000 })
}

/** People, for turning an id into a name and for "assign to". Staff may read this list. */
export function usePeople() {
  return $api.useQuery('get', '/api/core/users', {}, { staleTime: 60_000 })
}

export function useStatusLabel(): (code: string) => string {
  const definitions = useDefinitions()
  return (code) => definitions.data?.statuses.find((s) => s.code === code)?.label ?? humanize(code)
}

export function useNameOf(): (id: string | null | undefined) => string | null {
  const people = usePeople()
  return (id) => (id ? (people.data?.find((p) => p.id === id)?.name ?? 'Someone') : null)
}

/** `no_matching_request` -> `No matching request`. For codes with no label of their own. */
export function humanize(code: string): string {
  const words = code.replaceAll('_', ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

export type Tone = 'neutral' | 'accent' | 'danger' | 'warn' | 'ok'

/** How a status reads at a glance. Only the tone is decided here; the words are the API's. */
export function statusTone(code: string): Tone {
  if (code.endsWith('_overdue')) return 'danger'
  if (
    code === 'production_unreviewed' ||
    code === 'new_production' ||
    code === 'no_records_claimed'
  )
    return 'warn'
  if (code === 'production_received' || code === 'closed') return 'ok'
  if (
    code === 'pending_filing' ||
    code === 'waiting_for_approval' ||
    code === 'withdrawn' ||
    code === 'denied'
  )
    return 'neutral'
  return 'accent'
}

/** "+3d" overdue, "10d" to go: the docket's days figure, from the API's signed number. */
export function daysFigure(days: number | null | undefined): { text: string; overdue: boolean } {
  if (days === null || days === undefined) return { text: '—', overdue: false }
  if (days < 0) return { text: `+${-days}d`, overdue: true }
  return { text: days === 0 ? 'today' : `${days}d`, overdue: false }
}

export const SENSITIVITY: Record<string, string> = {
  public: 'Public',
  internal: 'Internal',
  privileged: 'Privileged',
}

export const CHANNEL: Record<string, string> = {
  email: 'Email',
  portal: 'Portal',
  mail: 'Post',
  call: 'Phone call',
  in_person: 'Meeting or visit',
  system: 'System',
}

export function fileSize(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export function money(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) return '—'
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(cents / 100)
}
