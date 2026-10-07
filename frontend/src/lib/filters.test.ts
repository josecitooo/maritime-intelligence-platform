import { describe, expect, it } from 'vitest'
import {
  applyFilters,
  bucketMotion,
  fleetSummary,
  isWaiting,
  NO_FILTER,
  passesFilters,
  portSummary,
  typeFacets,
} from './filters'
import type { PositionLatest } from '../api/types'

function p(mmsi: number, opts: Partial<PositionLatest> = {}): PositionLatest {
  return {
    mmsi,
    timestamp: '2026-10-07T12:00:00Z',
    latitude: 0,
    longitude: 0,
    sog: 0,
    cog: 0,
    heading: 0,
    nav_status: 0,
    ship_name: `B${mmsi}`,
    flags: [],
    ship_type: null,
    ...opts,
  }
}

describe('filters', () => {
  it('classifies waiting vs moving', () => {
    expect(isWaiting(p(1, { sog: 0.1, nav_status: 0 }))).toBe(true)
    expect(isWaiting(p(2, { sog: 5, nav_status: 1 }))).toBe(true)
    expect(isWaiting(p(3, { sog: 5, nav_status: 5 }))).toBe(true)
    expect(isWaiting(p(4, { sog: 5, nav_status: 0 }))).toBe(false)
    expect(bucketMotion(p(5, { sog: null, nav_status: null }))).toBe('unknown')
  })
  it('applies filters together', () => {
    const rows = [
      p(1, { ship_type: 70, sog: 5 }),
      p(2, { ship_type: 70, sog: 0.1 }),
      p(3, { ship_type: 30, sog: 5 }),
    ]
    expect(applyFilters(rows, 'B1', NO_FILTER).length).toBe(1)
    expect(passesFilters(p(2, { ship_type: 70 }), { motion: 'waiting', shipType: 70 })).toBe(true)
  })
  it('facets, fleet summary and port summary', () => {
    const rows = [p(1, { ship_type: 70, sog: 5 }), p(2, { ship_type: 70, sog: 0.1 }), p(3, { ship_type: 30, sog: null, nav_status: null })]
    expect(typeFacets(rows).find(f => f.code === 70)?.count).toBe(2)
    expect(fleetSummary(rows)).toEqual({ waiting: 1, moving: 1, unknown: 1 })
    expect(portSummary([
      { port: { id: 1, ne_id: 1, name: 'A', country: 'X', latitude: 0, longitude: 0 }, radius_km: 50, since: '2026-10-07T00:00:00Z', total: 2, waiting: 1, moving: 1, sampled_at: '2026-10-07T11:00:00Z' },
      { port: { id: 2, ne_id: 2, name: 'B', country: 'Y', latitude: 0, longitude: 0 }, radius_km: 50, since: '2026-10-07T00:00:00Z', total: 0, waiting: 0, moving: 0, sampled_at: null },
    ])).toEqual({ waiting: 1, active: 1 })
  })
})