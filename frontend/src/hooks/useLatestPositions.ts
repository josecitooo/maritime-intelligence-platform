/**
 * The positions the map draws, and *when* they are re-read.
 *
 * `useSyncPositions` is the window-sync rule in `./useSyncOnWindow` applied to
 * `/positions/latest`; the ports and track queries follow the same rule from
 * their own hooks. Fetching once and waiting for `last_flush` to move keeps a
 * 30 s poll from re-downloading the same dataset up to 59 times per
 * ingestion interval — see `./useSyncOnWindow` for the recovery exception.
 */

import { useQuery } from '@tanstack/react-query'
import { fetchLatestPositions } from '../api/client'
import type { HealthQuery } from './useHealth'
import { useSyncOnWindow } from './useSyncOnWindow'

const POSITIONS_KEY = ['positions', 'latest'] as const

/** Reads the newest position per vessel. Fetches once, then waits to be told. */
export function useLatestPositions() {
  return useQuery({
    queryKey: POSITIONS_KEY,
    queryFn: ({ signal }) => fetchLatestPositions(undefined, signal),
  })
}

/** Re-read the fleet when a new window lands or a failed read heals. */
export function useSyncPositions(health: HealthQuery): void {
  useSyncOnWindow(health, POSITIONS_KEY)
}

/** The shared query key, exported for tests that must invalidate it. */
export { POSITIONS_KEY }