import { QueryClient } from '@tanstack/react-query'
import createQueryHooks from 'openapi-react-query'

import { api } from './client'

/** Typed `useQuery` / `useMutation` over the API's own paths: `$api.useQuery('get', '/api/...')`. */
export const $api = createQueryHooks(api)

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // A refusal is an answer, not a blip; only a network failure is worth asking again.
      retry: (failures, error) => failures < 2 && statusOf(error) === undefined,
      staleTime: 15_000,
      refetchOnWindowFocus: false,
    },
  },
})

export interface ApiError {
  status?: number
  detail?: string
  code?: string
  tracking_number?: string | null
}

/**
 * openapi-react-query rejects with the response BODY, which has no status. `errorOf` reads what
 * the API puts in a body; where the status matters (409 against 403) the caller checks the body's
 * own fields, which the API keeps distinct.
 */
export function errorOf(error: unknown): ApiError {
  if (error && typeof error === 'object') {
    const body = error as Record<string, unknown>
    return {
      status: typeof body.status === 'number' ? body.status : undefined,
      detail: typeof body.detail === 'string' ? body.detail : undefined,
      code: typeof body.code === 'string' ? body.code : undefined,
      tracking_number: typeof body.tracking_number === 'string' ? body.tracking_number : null,
    }
  }
  return {}
}

function statusOf(error: unknown): number | undefined {
  const body = errorOf(error)
  return body.status ?? (body.detail ? 400 : undefined)
}

/** The sentence to show for a failed action: the API's own words when it sent some. */
export function messageOf(error: unknown, fallback = 'Something went wrong. Try again.'): string {
  return errorOf(error).detail ?? fallback
}
