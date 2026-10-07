/**
 * The positions the map draws, and *when* they are re-read.
 *
 * `docs/architecture.md` §9: the client polls `/health` and refetches
 * `/positions/latest` only when `last_flush` advances — that value changes
 * exactly when the worker commits a window, which is the only moment new
 * rows can exist. Polling positions on a timer would re-download the same
 * dataset up to 59 times per ingestion interval.
 *
 * One exception keeps that rule from stranding the client: a read that
 * failed while the API was dark is retried as soon as `/health` answers
 * again. Without it, the first outage would leave the map pinned to an error
 * until a page reload, because a `last_flush` change can only arrive over a
 * connection that recovered on its own.
 */

import { useEffect, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, fetchLatestPositions } from '../api/client'
import type { HealthQuery } from './useHealth'

const POSITIONS_KEY = ['positions', 'latest'] as const

/** Reads the newest position per vessel. Fetches once, then waits to be told. */
export function useLatestPositions() {
  return useQuery({
    queryKey: POSITIONS_KEY,
    queryFn: ({ signal }) => fetchLatestPositions(undefined, signal),
  })
}

/**
 * Re-read `/positions/latest` when either of two things happens:
 *
 * - `last_flush` moves to a value the client has not seen (a new window).
 *   The first non-null value is recorded but not acted on: the query already
 *   fetches on mount, and invalidating there would issue the same request
 *   twice. React 19 StrictMode double-invokes effects, so the guard is
 *   written to be idempotent — the second run sees the same timestamp.
 * - the positions query is in `error` while `/health` is answering, which is
 *   how a failed read heals after the API comes back.
 *
 * Everything else — a health poll returning the same window, a background
 * tab, a focus event — deliberately leaves the positions query alone.
 */
export function useSyncPositions(health: HealthQuery): void {
  const queryClient = useQueryClient()
  const previousFlush = useRef<string | null>(null)

  useEffect(() => {
    if (health.isPending) return

    const flush = health.data?.last_flush ?? null
    const state = queryClient.getQueryState(POSITIONS_KEY)
    // A 4xx is a definitive answer from a server that is there: re-asking
    // every poll would loop on a configuration problem only a human can fix
    // (a rotated key, a missing `VITE_API_KEY`). Transport failures and 5xx
    // are worth another attempt once `/health` is answering again.
    const refused = state?.error instanceof ApiError && state.error.status < 500
    const positionsFailed = state?.status === 'error' && !refused
    const newWindow =
      flush !== null && previousFlush.current !== null && flush !== previousFlush.current

    if (flush !== null) previousFlush.current = flush

    if (positionsFailed || newWindow) {
      void queryClient.invalidateQueries({ queryKey: POSITIONS_KEY })
    }
  }, [health.data, health.isPending, queryClient])
}
