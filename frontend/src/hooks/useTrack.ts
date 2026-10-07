/**
 * One vessel's stored track, fetched on demand.
 *
 * The polyline is read only while the operator asks for it (`enabled`), and
 * it answers once per vessel: a track never changes unless new windows land,
 * and the track query lives alongside the port queries under the same
 * window-sync rule. An empty list is a correct answer about a vessel with no
 * stored positions, and the map simply draws nothing.
 */

import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchVesselTrack } from '../api/client'
import type { HealthQuery } from './useHealth'
import { useSyncOnWindow } from './useSyncOnWindow'

/**
 * The track for `mmsi`, fetched only while the panel asks for it. Pass a null
 * MMSI (or `enabled=false`) to keep the query dormant without clearing state.
 */
export function useVesselTrack(mmsi: number | null, enabled: boolean) {
  return useQuery({
    queryKey: ['vessels', mmsi, 'track'],
    queryFn: ({ signal }) => fetchVesselTrack(mmsi as number, signal),
    enabled: enabled && mmsi !== null,
  })
}

/** Re-read an open track when a new window lands or a failed read heals. */
export function useSyncTrack(health: HealthQuery, mmsi: number | null): void {
  const key = useMemo(() => ['vessels', mmsi, 'track'] as const, [mmsi])
  useSyncOnWindow(health, key)
}