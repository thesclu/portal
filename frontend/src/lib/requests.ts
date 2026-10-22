import { api } from '../api/client'
import { unwrap } from '../components/ActionDialog'

/** The one request with exactly this tracking number, among those the person may see. */
export async function findRequest(tracking: string) {
  const wanted = tracking.trim().toLowerCase()
  const found = unwrap(
    await api.GET('/api/records/requests', { params: { query: { q: wanted, limit: 5 } } }),
  )
  const target = found.items.find((item) => item.tracking_number.toLowerCase() === wanted)
  if (!target) throw new Error('No request you can see has that tracking number.')
  return target
}
