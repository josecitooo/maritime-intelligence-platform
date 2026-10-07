/**
 * Fleet filters — the shared vocabulary of the filter bar and the KPIs.
 *
 * Every decision is browser-side over exactly what `/positions/latest`
 * answered, and mirrors the backend's own verdict in `app/ports.py`: a vessel
 * is *waiting* when AIS reports it anchored, moored, or moving at less than
 * half a knot. `moving` is the same column negation plus an evidence rule — a
 * row with neither a status nor a speed is *unknown*, not moving.
 */

import type { PortCongestionSummary, PositionLatest } from '../api/types'

/** The slice of a row that decides motion — shared by the fleet and the ports. */
export interface MotionEvidence {
  sog: number | null
  nav_status: number | null
}

/** A vessel is not under way when its status says so or its speed says so. */
export function isWaiting(row: MotionEvidence): boolean {
  return (row.sog !== null && row.sog < 0.5) || row.nav_status === 1 || row.nav_status === 5
}

/** A vessel that reports nothing about its motion is not classifiable as either. */
export function hasMotionReport(row: MotionEvidence): boolean {
  return row.nav_status !== null || row.sog !== null
}

/** How the fleet splits. `moving` keeps its own evidence rule (see above). */
export type MotionBucket = 'waiting' | 'moving' | 'unknown'

export function bucketMotion(row: PositionLatest): MotionBucket {
  if (isWaiting(row)) return 'waiting'
  return hasMotionReport(row) ? 'moving' : 'unknown'
}

/** The active filters: motion bucket plus a ship-type code (null = any type). */
export interface FleetFilters {
  motion: MotionBucket | 'all'
  shipType: number | null
}

export const NO_FILTER: FleetFilters = { motion: 'all', shipType: null }

/** Name or MMSI search — the same rule from before the filters existed. */
export function matchesSearch(row: PositionLatest, query: string): boolean {
  const needle = query.trim().toLowerCase()
  if (needle === '') return true
  if (row.ship_name?.toLowerCase().includes(needle)) return true
  return String(row.mmsi).includes(needle)
}

/** Does the row survive every active filter? */
export function passesFilters(row: PositionLatest, filters: FleetFilters): boolean {
  if (filters.motion !== 'all' && bucketMotion(row) !== filters.motion) return false
  if (filters.shipType !== null && row.ship_type !== filters.shipType) return false
  return true
}

/** The whole pipeline the stage applies to one fetched window. */
export function applyFilters(
  rows: PositionLatest[],
  query: string,
  filters: FleetFilters,
): PositionLatest[] {
  return rows.filter((row) => matchesSearch(row, query) && passesFilters(row, filters))
}

/** The present ship types with a vessel count, for the filter bar's select. */
export function typeFacets(rows: PositionLatest[]): Array<{ code: number; count: number }> {
  const counts = new Map<number, number>()
  for (const row of rows) {
    if (row.ship_type !== null) counts.set(row.ship_type, (counts.get(row.ship_type) ?? 0) + 1)
  }
  return [...counts.entries()]
    .map(([code, count]) => ({ code, count }))
    .sort((a, b) => a.code - b.code)
}

/** The fleet split the KPI strip shows over one set of rows. */
export interface FleetSummary {
  waiting: number
  moving: number
  unknown: number
}

export function fleetSummary(rows: PositionLatest[]): FleetSummary {
  let waiting = 0
  let moving = 0
  let unknown = 0
  for (const row of rows) {
    const bucket = bucketMotion(row)
    if (bucket === 'waiting') waiting += 1
    else if (bucket === 'moving') moving += 1
    else unknown += 1
  }
  return { waiting, moving, unknown }
}

/** The port side of the strip: waiting summed plus how many ports hold vessels. */
export interface PortSummary {
  waiting: number
  active: number
}

export function portSummary(ports: PortCongestionSummary[]): PortSummary {
  let waiting = 0
  let active = 0
  for (const row of ports) {
    if (row.sampled_at === null) continue
    waiting += row.waiting
    active += 1
  }
  return { waiting, active }
}