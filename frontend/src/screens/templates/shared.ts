import { useQueryClient } from '@tanstack/react-query'
import type { ReactNode } from 'react'

import type { ActionSpec, Values } from '../../components/ActionDialog'

/** A dialog field's value, trimmed: for names and other one-line answers. */
export const text = (values: Values, name: string) => String(values[name] ?? '').trim()

/** A dialog field's value as typed: for the words of a letter, where a line break is content. */
export const raw = (values: Values, name: string) => String(values[name] ?? '')

/**
 * Re-reads everything under /api/drafting. One edit reaches several lists (renaming a shared
 * block changes whether every template using it has unsaved changes), so nothing here tries to
 * pick which.
 */
export function useRefreshLibrary(): () => Promise<void> {
  const client = useQueryClient()
  return () =>
    client.invalidateQueries({
      predicate: (query) => {
        const path = query.queryKey[1]
        return typeof path === 'string' && path.startsWith('/api/drafting/')
      },
    })
}

/** A dialog with nothing to fill in: a sentence, and one button that does the thing. */
export function confirm(options: {
  key: string
  title: string
  intro: ReactNode
  submit: string
  danger?: boolean
  run: () => Promise<void>
}): ActionSpec {
  return { ...options, fields: [] }
}

export function messageFrom(caught: unknown): string {
  return caught instanceof Error ? caught.message : 'Something went wrong. Try again.'
}

/** How many filed letters came from a template or from one revision of it. */
export function used(count: number): string {
  return count === 0 ? 'Not used yet' : count === 1 ? 'Used once' : `Used ${count} times`
}

/** The same count as the header line says it: "used 52 times". */
export function usedTimes(count: number): string {
  return count === 0 ? 'not used yet' : count === 1 ? 'used once' : `used ${count} times`
}

/** How many filed letters came from one revision, as the Revisions tab says it. */
export function filings(count: number): string {
  return count === 0 ? '—' : count === 1 ? '1 filing' : `${count} filings`
}
