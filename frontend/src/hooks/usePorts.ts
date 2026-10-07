/**
 * Congestion data — the port layer and the window rule behind it.
 *
 * The summary every map marker reads, and the detail one port's panel reads,
 * both derive from stored positions at read time (PostGIS, `app/ports.py`).
 * They refetch on the same `last_flush` rule as the fleet, so the port layer
 * ages with the map instead of on its own timer: invalidating the `['ports']`
 * prefix at flush time refreshes the summary and any open detail together.
 */

import { useQuery } from '@tanstack/react-query'
import { fetchPortCongestion, fetchPortDetail } from '../api/client'
import type { HealthQuery } from './useHealth'
import { useSyncOnWindow } from './useSyncOnWindow'

const PORTS_KEY = ['ports'] as const

/** Every port's reading: the payload the map's markers derive from. */
export function usePortCongestion() {
  return useQuery({
    queryKey: PORTS_KEY,
    queryFn: ({ signal }) => fetchPortCongestion(signal),
  })
}

/** Re-read the port layer when a new window lands or a failed read heals. */
export function useSyncPorts(health: HealthQuery): void {
  useSyncOnWindow(health, PORTS_KEY)
}

/** The detail for one port, fetched only while its panel is open. */
export function usePortDetail(portId: number | null) {
  return useQuery({
    queryKey: ['ports', portId, 'congestion'],
    queryFn: ({ signal }) => fetchPortDetail(portId as number, signal),
    enabled: portId !== null,
  })
}