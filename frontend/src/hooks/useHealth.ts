/**
 * Liveness polling.
 *
 * `/health` is the cheapest endpoint the API exposes and the only signal a
 * browser has that the worker is still flushing and the database is still
 * reachable (`docs/architecture.md` §11). It polls on a timer; the positions
 * query does not — see `./useLatestPositions`.
 */

import { useQuery } from '@tanstack/react-query'
import { ApiError, fetchHealth } from '../api/client'

/** One liveness round trip every 30 s: cheap, and far below any load concern. */
const HEALTH_POLL_MS = 30_000

/** What the status pill shows, derived from the health query alone. */
export type ApiState = 'connecting' | 'ok' | 'degraded' | 'unreachable'

/** The health query's shape, named once so the other hooks can take it. */
export type HealthQuery = ReturnType<typeof useHealth>

/**
 * Collapse the query into the four states the UI can be in.
 *
 * `ApiError` with 503 is a *reply*, not a failure: the API is up and its
 * database is not, which is `degraded`. A `TypeError` from `fetch` means the
 * transport never answered, which is `unreachable`. The distinction is the
 * whole reason `ApiError` carries its status.
 */
export function resolveApiState(query: HealthQuery): ApiState {
  if (query.isPending) return 'connecting'
  if (query.error) {
    if (query.error instanceof ApiError && query.error.status === 503) return 'degraded'
    return 'unreachable'
  }
  if (!query.data) return 'unreachable'
  return query.data.status === 'healthy' ? 'ok' : 'degraded'
}

/** Polls `/health`. Polling stops while the tab is in the background. */
export function useHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: ({ signal }) => fetchHealth(signal),
    refetchInterval: HEALTH_POLL_MS,
  })
}
