/**
 * The positions the map draws, and *when* they are re-read.
 *
 * `docs/architecture.md` §9: the client polls `/health` and refetches
 * `/positions/latest` only when `last_flush` advances — that value changes
 * exactly when the worker commits a window, which is the only moment new
 * rows can exist. Polling positions on a timer would re-download the same
 * dataset up to 59 times per ingestion interval.
 */

import { useEffect, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchLatestPositions } from '../api/client'

export const POSITIONS_KEY = ['positions', 'latest'] as const

/** Reads the newest position per vessel. Fetches once, then waits to be told. */
export function useLatestPositions() {
  return useQuery({
    queryKey: POSITIONS_KEY,
    queryFn: ({ signal }) => fetchLatestPositions(undefined, signal),
  })
}

/**
 * Invalidate the positions query whenever `last_flush` moves to a value the
 * client has not seen.
 *
 * The first non-null value is recorded but not acted on: the query already
 * fetches on mount, and invalidating it there would issue the same request
 * twice. React 19 StrictMode double-invokes effects, so the guard is written
 * to be idempotent — the second run sees the same timestamp and does nothing.
 */
export function useRefetchOnNewWindow(lastFlush: string | null | undefined): void {
  const queryClient = useQueryClient()
  const previous = useRef<string | null>(null)

  useEffect(() => {
    if (!lastFlush) return
    if (previous.current !== null && previous.current !== lastFlush) {
      void queryClient.invalidateQueries({ queryKey: POSITIONS_KEY })
    }
    previous.current = lastFlush
  }, [lastFlush, queryClient])
}
