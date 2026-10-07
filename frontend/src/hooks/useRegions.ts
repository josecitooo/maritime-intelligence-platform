/**
 * The region catalog — read on a cadence, flipped optimistically.
 *
 * Reads poll every 30 s so a selection made in another browser shows up here
 * without a reload. Writes are optimistic with rollback: the toggle feels
 * instant, and a failure puts the chips back to where they were rather than
 * leaving the UI claiming a state the database never accepted.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchRegions, updateRegions } from '../api/client'
import type { RegionToggle } from '../api/client'
import type { Region } from '../api/types'

const REGIONS_KEY = ['regions'] as const

/** One catalog poll per 30 s: matches the health poll and the worker's cadence. */
const REGIONS_POLL_MS = 30_000

export function useRegions() {
  return useQuery({
    queryKey: REGIONS_KEY,
    queryFn: ({ signal }) => fetchRegions(signal),
    refetchInterval: REGIONS_POLL_MS,
  })
}

export function useToggleRegions() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (flips: RegionToggle[]) => updateRegions(flips),
    onMutate: async (flips) => {
      const key: typeof REGIONS_KEY = REGIONS_KEY
      await queryClient.cancelQueries({ queryKey: key })
      const previous = queryClient.getQueryData<Region[]>(key)
      if (previous) {
        queryClient.setQueryData<Region[]>(
          key,
          previous.map((region) => {
            const flip = flips.find((candidate) => candidate.name === region.name)
            return flip ? { ...region, enabled: flip.enabled } : region
          }),
        )
      }
      return { previous }
    },
    onError: (_error, _flips, context) => {
      if (context?.previous) queryClient.setQueryData(REGIONS_KEY, context.previous)
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: REGIONS_KEY })
    },
  })
}