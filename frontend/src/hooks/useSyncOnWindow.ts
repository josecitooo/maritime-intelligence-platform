/**
 * The window-sync rule shared by the flush-derived queries.
 *
 * `docs/architecture.md` §9: the client polls `/health` and refetches a
 * data query only when `last_flush` advances — that value changes exactly
 * when the worker commits a window, the only moment new rows can exist.
 * Polling data on a timer would re-download the same dataset up to 59 times
 * per ingestion interval.
 *
 * One exception keeps that rule from stranding the client: a query that
 * failed while the API was dark is retried as soon as `/health` answers
 * again. Without it, the first outage would leave the map pinned to an error
 * until a page reload, because a `last_flush` change can only arrive over a
 * connection that recovered on its own.
 */
import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ApiError } from '../api/client'
import type { HealthQuery } from './useHealth'

/** Invalidates a query key when a new window lands or a failed read heals. */
export function useSyncOnWindow(health: HealthQuery, key: readonly unknown[]): void {
  const queryClient = useQueryClient()
  const previousFlush = useRef<string | null>(null)

  useEffect(() => {
    if (health.isPending) return

    const flush = health.data?.last_flush ?? null
    const state = queryClient.getQueryState(key)
    // A 4xx is a definitive answer from a server that is there: re-asking
    // every poll would loop on a configuration problem only a human can fix
    // (a rotated key, a missing `VITE_API_KEY`). Transport failures and 5xx
    // are worth another attempt once `/health` is answering again.
    const refused = state?.error instanceof ApiError && state.error.status < 500
    const queryFailed = state?.status === 'error' && !refused
    const newWindow =
      flush !== null && previousFlush.current !== null && flush !== previousFlush.current

    if (flush !== null) previousFlush.current = flush

    if (queryFailed || newWindow) {
      void queryClient.invalidateQueries({ queryKey: key })
    }
  }, [health.data, health.isPending, key, queryClient])
}